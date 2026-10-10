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
 * @file Button.h
 *
 * Reuses the on-board BOOT button (GPIO0 on the ESP32-S3 side) as an
 * application key: long press triggers a Matter factory reset, short press
 * re-opens the commissioning window.
 *
 **/

#pragma once

void InitButton();
