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

#include "DebugShell.h"

#include "sdkconfig.h"

#if CONFIG_ENABLE_CHIP_SHELL

#include <lib/core/CHIPError.h>
#include <lib/shell/Engine.h>
#include <lib/shell/streamer.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include <stdlib.h>
#include <string.h>

using chip::Shell::streamer_get;
using chip::Shell::streamer_printf;

#if CONFIG_FREERTOS_USE_TRACE_FACILITY && CONFIG_FREERTOS_USE_STATS_FORMATTING_FUNCTIONS

// `matter tasks`: lists every task with its stack high water mark.
//
// The "Stack" column is the least free stack space (in bytes) the task has had since it was started.
// It only ever shrinks, so read it after exercising the path of interest (e.g. creating a Thread network);
// peak usage = configured stack size - this value.
static CHIP_ERROR TasksHandler(int argc, char * argv[])
{
    constexpr size_t kBytesPerTask = 64; // vTaskList prints roughly 40-60 bytes per task
    const size_t bufSize           = uxTaskGetNumberOfTasks() * kBytesPerTask + 128;
    char * buf                     = static_cast<char *>(malloc(bufSize));
    if (buf == nullptr)
    {
        return CHIP_ERROR_NO_MEMORY;
    }
    vTaskList(buf);
    streamer_printf(streamer_get(), "Name           State Prio Stack Num Core\n");
    // streamer_printf 的行缓冲区只有 256 字节左右,整段一次打印会被截断,所以逐行输出
    for (char * line = buf; *line != '\0';)
    {
        char * end = strchr(line, '\n');
        if (end != nullptr)
        {
            *end = '\0';
        }
        streamer_printf(streamer_get(), "%s\n", line);
        if (end == nullptr)
        {
            break;
        }
        line = end + 1;
    }
    free(buf);
    return CHIP_NO_ERROR;
}

#else

static CHIP_ERROR TasksHandler(int argc, char * argv[])
{
    streamer_printf(streamer_get(), "FreeRTOS trace facility is not enabled in this build\n");
    return CHIP_NO_ERROR;
}

#endif // CONFIG_FREERTOS_USE_TRACE_FACILITY && CONFIG_FREERTOS_USE_STATS_FORMATTING_FUNCTIONS

void RegisterDebugShellCommands()
{
    static const chip::Shell::shell_command_t cmds[] = {
        {
            .cmd_func = TasksHandler,
            .cmd_name = "tasks",
            .cmd_help = "List FreeRTOS tasks with their stack high water mark",
        },
    };
    chip::Shell::Engine::Root().RegisterCommands(cmds, sizeof(cmds) / sizeof(chip::Shell::shell_command_t));
}

#else

void RegisterDebugShellCommands() {}

#endif // CONFIG_ENABLE_CHIP_SHELL
