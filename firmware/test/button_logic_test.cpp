// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Googol Lee
//
// ButtonLogic 的主机端单元测试。运行:make fw-test(不需要 ESP-IDF)。

#include <cstdio>
#include <cstdlib>

#include "../main/include/ButtonLogic.h"

static int sFailures = 0;
static int sChecks   = 0;

#define CHECK_EQ(actual, expected)                                                                                         \
    do                                                                                                                     \
    {                                                                                                                      \
        ++sChecks;                                                                                                         \
        if ((actual) != (expected))                                                                                        \
        {                                                                                                                  \
            std::printf("FAIL %s:%d  %s != %s\n", __FILE__, __LINE__, #actual, #expected);                                 \
            ++sFailures;                                                                                                   \
        }                                                                                                                  \
    } while (0)

using A = ButtonAction;

static void IdleLevelDoesNothing()
{
    ButtonLogic l;
    CHECK_EQ(l.OnSettledLevel(false), A::kNone);
    CHECK_EQ(l.OnSettledLevel(false), A::kNone);
}

static void PressThenReleaseIsShortPress()
{
    ButtonLogic l;
    CHECK_EQ(l.OnSettledLevel(true), A::kStartLongPressTimer);
    CHECK_EQ(l.OnSettledLevel(false), A::kShortPress);
    CHECK_EQ(l.OnSettledLevel(false), A::kNone);
}

static void RepeatedPressedLevelStartsTimerOnce()
{
    ButtonLogic l;
    CHECK_EQ(l.OnSettledLevel(true), A::kStartLongPressTimer);
    CHECK_EQ(l.OnSettledLevel(true), A::kNone);
}

static void HeldUntilExpiryIsFactoryResetAndReleaseIsNotShortPress()
{
    ButtonLogic l;
    CHECK_EQ(l.OnSettledLevel(true), A::kStartLongPressTimer);
    CHECK_EQ(l.OnLongPressExpired(true), A::kFactoryReset);
    CHECK_EQ(l.OnSettledLevel(false), A::kNone); // the press was consumed by the reset
    CHECK_EQ(l.OnLongPressExpired(false), A::kNone);
}

static void ExpiryFiresOnlyOnce()
{
    ButtonLogic l;
    l.OnSettledLevel(true);
    CHECK_EQ(l.OnLongPressExpired(true), A::kFactoryReset);
    CHECK_EQ(l.OnLongPressExpired(true), A::kNone);
}

static void ExpiryWithoutPressIsIgnored()
{
    ButtonLogic l;
    CHECK_EQ(l.OnLongPressExpired(true), A::kNone);
}

static void ExpiryWhenPinAlreadyHighIsIgnoredAndReleaseStillShortPress()
{
    // Timer ran out but the pin has been released in the meantime (not yet seen by the
    // debounced path): no reset, and the release is still a normal short press.
    ButtonLogic l;
    l.OnSettledLevel(true);
    CHECK_EQ(l.OnLongPressExpired(false), A::kNone);
    CHECK_EQ(l.OnSettledLevel(false), A::kShortPress);
}

static void ExpiryDuringDebounceAfterReleaseIsIgnored()
{
    // Task order when an expiry arrives during the debounce wait: settle the level first,
    // then handle the expiry with a fresh read. A release must win.
    ButtonLogic l;
    l.OnSettledLevel(true);
    CHECK_EQ(l.OnSettledLevel(false), A::kShortPress);
    CHECK_EQ(l.OnLongPressExpired(false), A::kNone);
}

static void SecondPressAfterResetStartsANewCycle()
{
    ButtonLogic l;
    l.OnSettledLevel(true);
    l.OnLongPressExpired(true);
    l.OnSettledLevel(false);
    CHECK_EQ(l.OnSettledLevel(true), A::kStartLongPressTimer);
    CHECK_EQ(l.OnSettledLevel(false), A::kShortPress);
}

int main()
{
    IdleLevelDoesNothing();
    PressThenReleaseIsShortPress();
    RepeatedPressedLevelStartsTimerOnce();
    HeldUntilExpiryIsFactoryResetAndReleaseIsNotShortPress();
    ExpiryFiresOnlyOnce();
    ExpiryWithoutPressIsIgnored();
    ExpiryWhenPinAlreadyHighIsIgnoredAndReleaseStillShortPress();
    ExpiryDuringDebounceAfterReleaseIsIgnored();
    SecondPressAfterResetStartsANewCycle();

    if (sFailures != 0)
    {
        std::printf("%d of %d checks failed\n", sFailures, sChecks);
        return EXIT_FAILURE;
    }
    std::printf("button_logic_test: %d checks passed\n", sChecks);
    return EXIT_SUCCESS;
}
