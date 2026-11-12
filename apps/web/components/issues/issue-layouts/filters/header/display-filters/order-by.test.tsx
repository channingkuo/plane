/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import { Menu, MenuContent, MenuTrigger } from "@makeplane/propel/components/menu";
import type { TIssueOrderByOptions } from "@plane/types";
import { FilterOrderBy } from "./order-by";

vi.mock("@plane/i18n", () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

// jsdom does not implement the Web Animations API used by the menu viewport.
// oxlint-disable-next-line no-extend-native
beforeAll(() => Object.defineProperty(Element.prototype, "getAnimations", { value: () => [], configurable: true }));

const orderByOptions: TIssueOrderByOptions[] = ["sort_order", "workflow_order", "-created_at", "start_date"];
function mount(selectedOrderBy: TIssueOrderByOptions) {
  const handleUpdate = vi.fn();
  render(
    <Menu defaultOpen>
      <MenuTrigger>Display</MenuTrigger>
      <MenuContent>
        <FilterOrderBy selectedOrderBy={selectedOrderBy} handleUpdate={handleUpdate} orderByOptions={orderByOptions} />
      </MenuContent>
    </Menu>
  );
  fireEvent.click(screen.getByText("common.order_by.label"));
  return handleUpdate;
}

describe("timeline ordering menu", () => {
  it("selects requirement ordering with its fixed ascending direction from a descending field", async () => {
    const update = mount("-created_at");
    fireEvent.click(await screen.findByRole("menuitemradio", { name: "common.order_by.requirement_start_date" }));
    expect(update).toHaveBeenCalledWith("workflow_order");
  });

  it("hides direction controls for requirement ordering and restores field defaults when leaving it", async () => {
    const update = mount("workflow_order");
    await screen.findByRole("menuitemradio", { name: "common.order_by.requirement_start_date" });
    expect(screen.queryByRole("menuitemradio", { name: "common.order_by.desc" })).toBeNull();
    expect(screen.queryByRole("menuitemradio", { name: "common.order_by.asc" })).toBeNull();
    fireEvent.click(screen.getByRole("menuitemradio", { name: "common.order_by.last_created" }));
    expect(update).toHaveBeenCalledWith("-created_at");
  });
});
