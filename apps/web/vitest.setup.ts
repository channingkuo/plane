/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(cleanup);

class TestResizeObserver implements ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

Object.defineProperty(window, "ResizeObserver", { value: TestResizeObserver, configurable: true });

// jsdom 23 predates PointerEvent; Base UI dispatches pointer-backed checkbox clicks.
if (!window.PointerEvent) Object.defineProperty(window, "PointerEvent", { value: MouseEvent, configurable: true });
