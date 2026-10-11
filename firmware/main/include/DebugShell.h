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
 * @file DebugShell.h
 *
 * Debug-only commands for the Matter shell. Only compiled in when the shell is
 * enabled (`debug_shell: true` in config/device.yaml); release builds contain none of it.
 *
 **/

#pragma once

/** Registers the debug commands (currently `matter tasks`). No-op unless the shell is enabled. */
void RegisterDebugShellCommands();
