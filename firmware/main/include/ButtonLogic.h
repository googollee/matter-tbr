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

/**
 * @file ButtonLogic.h
 *
 * The press / long-press decision logic of the BOOT button, with no ESP-IDF or Matter
 * dependency so it can be unit-tested on the host (see firmware/test, `make fw-test`).
 * Button.cpp feeds it debounced levels and long-press expiries, and performs the returned
 * action.
 *
 **/

#pragma once

enum class ButtonAction
{
    kNone,
    kStartLongPressTimer, // button went down: (re)arm the long-press timer
    kShortPress,          // button released before the long press fired: stop the timer, re-open the commissioning window
    kFactoryReset,        // long press expired while still held: schedule a factory reset
};

class ButtonLogic
{
public:
    // Called with the settled (debounced) level. Only a change of level produces an action.
    ButtonAction OnSettledLevel(bool pressed)
    {
        if (pressed == mPressed)
        {
            return ButtonAction::kNone;
        }
        mPressed = pressed;
        return pressed ? ButtonAction::kStartLongPressTimer : ButtonAction::kShortPress;
    }

    // Called when the long-press timer ran out. `levelLow` is a fresh read of the pin.
    // Fires only if the button is still held, and marks the press as consumed so the later
    // release is not treated as a short press.
    ButtonAction OnLongPressExpired(bool levelLow)
    {
        if (mPressed && levelLow)
        {
            mPressed = false;
            return ButtonAction::kFactoryReset;
        }
        return ButtonAction::kNone;
    }

private:
    // Idle level is high (internal pull-up; the button pulls the pin low when pressed).
    bool mPressed = false;
};
