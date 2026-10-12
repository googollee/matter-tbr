/*
 *
 *    Copyright (c) 2026 Googol Lee
 *    All rights reserved.
 *
 *    Licensed under the Apache License, Version 2.0 (the "License");
 *    you may not use this file except in compliance with the License.
 *    You may obtain a copy of the License at
 *
 *        http://www.apache.org/licenses/LICENSE-2.0
 *
 *    Unless required by applicable law or agreed to in writing, software
 *    distributed under the License is distributed on an "AS IS" BASIS,
 *    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 *    See the License for the specific language governing permissions and
 *    limitations under the License.
 */

#include "Button.h"
#include "ButtonLogic.h"

#include <driver/gpio.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>

#include <app/server/Server.h>
#include <platform/CHIPDeviceLayer.h>

using namespace ::chip;
using namespace ::chip::DeviceLayer;

namespace {

// GPIO0 is the board's BOOT button (ESP32-S3 side). It is a strapping pin
// only at power-on/reset; once the application is running it is a free GPIO.
constexpr gpio_num_t kButtonGpioNum = GPIO_NUM_0;

constexpr uint32_t kDebounceDelayMs     = 50;
constexpr int64_t kLongPressThresholdUs = INT64_C(5000000); // 5s
constexpr UBaseType_t kEventQueueDepth  = 16;

const char TAG[] = "button";

// All button state is only ever read or written from ButtonTask. The ISR and the esp_timer
// callback only ever push an event into the queue; they never touch shared state directly.
enum class ButtonEvent : uint8_t
{
    kEdge,             // a GPIO edge happened (not yet debounced)
    kLongPressExpired, // the long-press timer ran out
};

QueueHandle_t sButtonEventQueue;
esp_timer_handle_t sLongPressTimer;

void LogIfScheduleFailed(const char * what, CHIP_ERROR err)
{
    if (err != CHIP_NO_ERROR)
    {
        ESP_LOGE(TAG, "Failed to schedule %s: %" CHIP_ERROR_FORMAT, what, err.Format());
    }
}

void HandleFactoryReset(intptr_t /* context */)
{
    ESP_LOGW(TAG, "Button long press: scheduling factory reset");
    Server::GetInstance().ScheduleFactoryReset();
}

void HandleOpenCommissioningWindow(intptr_t /* context */)
{
    CommissioningWindowManager & commissionMgr = Server::GetInstance().GetCommissioningWindowManager();
    if (commissionMgr.IsCommissioningWindowOpen())
    {
        ESP_LOGI(TAG, "Button short press: commissioning window is already open, ignoring");
        return;
    }

    ESP_LOGI(TAG, "Button short press: re-opening commissioning window");
    CHIP_ERROR err = commissionMgr.OpenBasicCommissioningWindow();
    if (err != CHIP_NO_ERROR)
    {
        ESP_LOGE(TAG, "Failed to open commissioning window: %" CHIP_ERROR_FORMAT, err.Format());
    }
}

// Runs on the esp_timer task: only ever posts an event, never touches button state directly.
void LongPressTimerCallback(void * /* arg */)
{
    ButtonEvent event = ButtonEvent::kLongPressExpired;
    xQueueSend(sButtonEventQueue, &event, 0);
}

// Runs in interrupt context: no debouncing, no level read, just "something changed on this
// pin". ButtonTask is the only place that decides what the settled level actually is.
void IRAM_ATTR ButtonIsrHandler(void * /* arg */)
{
    ButtonEvent event                  = ButtonEvent::kEdge;
    BaseType_t higherPriorityTaskWoken = pdFALSE;
    xQueueSendFromISR(sButtonEventQueue, &event, &higherPriorityTaskWoken);
    portYIELD_FROM_ISR(higherPriorityTaskWoken);
}

void PerformAction(ButtonAction action)
{
    switch (action)
    {
    case ButtonAction::kNone:
        break;
    case ButtonAction::kStartLongPressTimer:
        ESP_LOGI(TAG, "Button pressed");
        esp_timer_stop(sLongPressTimer); // defensive: no-op unless a previous cycle left it armed
        esp_timer_start_once(sLongPressTimer, kLongPressThresholdUs);
        break;
    case ButtonAction::kShortPress:
        ESP_LOGI(TAG, "Button released");
        esp_timer_stop(sLongPressTimer); // no-op if it already fired (long press already handled)
        LogIfScheduleFailed("commissioning window re-open", PlatformMgr().ScheduleWork(HandleOpenCommissioningWindow, 0));
        break;
    case ButtonAction::kFactoryReset:
        LogIfScheduleFailed("factory reset", PlatformMgr().ScheduleWork(HandleFactoryReset, 0));
        break;
    }
}

void ButtonTask(void * /* param */)
{
    ButtonLogic logic;

    ButtonEvent event;
    while (true)
    {
        if (xQueueReceive(sButtonEventQueue, &event, portMAX_DELAY) != pdTRUE)
        {
            continue;
        }

        if (event == ButtonEvent::kLongPressExpired)
        {
            PerformAction(logic.OnLongPressExpired(gpio_get_level(kButtonGpioNum) == 0));
            continue;
        }

        // event == kEdge: let the pin settle, then trust only the level read afterwards,
        // never the edge itself, so a burst of bounce can never make us miss the eventual
        // stable state. Events queued up during the wait are drained, not individually
        // acted on; a kLongPressExpired among them is remembered instead of being dropped.
        vTaskDelay(pdMS_TO_TICKS(kDebounceDelayMs));
        bool longPressExpiredDuringDebounce = false;
        ButtonEvent drained;
        while (xQueueReceive(sButtonEventQueue, &drained, 0) == pdTRUE)
        {
            longPressExpiredDuringDebounce |= (drained == ButtonEvent::kLongPressExpired);
        }

        PerformAction(logic.OnSettledLevel(gpio_get_level(kButtonGpioNum) == 0));

        if (longPressExpiredDuringDebounce)
        {
            // The level is read afresh, so this correctly no-ops if the edge handling just
            // above already turned it into a release.
            PerformAction(logic.OnLongPressExpired(gpio_get_level(kButtonGpioNum) == 0));
        }
    }
}

} // namespace

void InitButton()
{
    gpio_config_t config = {};
    config.pin_bit_mask = 1ULL << kButtonGpioNum;
    config.mode         = GPIO_MODE_INPUT;
    config.pull_up_en   = GPIO_PULLUP_ENABLE;
    config.pull_down_en = GPIO_PULLDOWN_DISABLE;
    config.intr_type    = GPIO_INTR_ANYEDGE;
    ESP_ERROR_CHECK(gpio_config(&config));

    sButtonEventQueue = xQueueCreate(kEventQueueDepth, sizeof(ButtonEvent));
    if (sButtonEventQueue == nullptr)
    {
        ESP_LOGE(TAG, "Failed to create button event queue, button feature disabled");
        return;
    }

    esp_timer_create_args_t timerArgs = {};
    timerArgs.callback                = &LongPressTimerCallback;
    timerArgs.name                    = "btn_long_press";
    if (esp_timer_create(&timerArgs, &sLongPressTimer) != ESP_OK)
    {
        ESP_LOGE(TAG, "Failed to create long-press timer, button feature disabled");
        vQueueDelete(sButtonEventQueue);
        return;
    }

    esp_err_t err = gpio_install_isr_service(0);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) // INVALID_STATE = already installed by another component
    {
        ESP_ERROR_CHECK(err);
    }
    ESP_ERROR_CHECK(gpio_isr_handler_add(kButtonGpioNum, ButtonIsrHandler, nullptr));

    if (xTaskCreate(ButtonTask, "button", 3072, nullptr, 1, nullptr) != pdPASS)
    {
        ESP_LOGE(TAG, "Failed to create button task, button feature disabled");
        gpio_isr_handler_remove(kButtonGpioNum);
        esp_timer_delete(sLongPressTimer);
        vQueueDelete(sButtonEventQueue);
        return;
    }

    ESP_LOGI(TAG, "Button on GPIO%d ready: long press (>=%llds) = factory reset, short press = re-open commissioning window",
             static_cast<int>(kButtonGpioNum), static_cast<long long>(kLongPressThresholdUs / 1000000));
}
