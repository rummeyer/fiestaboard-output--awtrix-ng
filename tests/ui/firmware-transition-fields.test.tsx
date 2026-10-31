import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { SchemaForm, type JSONSchema } from "@/components/plugin-settings";

// Run inside the core web test harness, using the plugin's actual manifest.
const manifest = JSON.parse(readFileSync(resolve(process.env.TC002_PLUGIN_DIR!, "manifest.json"), "utf8"));
const fields = Object.fromEntries(Object.entries(manifest.output.settings_schema.properties)
  .filter(([name]) => name.startsWith("firmware_")));
const schema = { type: "object", properties: fields } as JSONSchema;

function Harness() {
  const [values, setValues] = useState<Record<string, unknown>>({});
  return <><SchemaForm schema={schema} values={values} onChange={setValues} />
    <output data-testid="values">{JSON.stringify(values)}</output></>;
}
const values = () => JSON.parse(screen.getByTestId("values").textContent!);

describe("TC002 firmware transition settings", () => {
  it("selects the named effect and can return to device settings", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("combobox", { name: "Firmware transition effect" }));
    await user.click(await screen.findByRole("option", { name: "Fade", exact: true }));
    expect(values().firmware_transition_effect).toBe("Fade");
    await user.click(screen.getByRole("combobox", { name: "Firmware transition effect" }));
    await user.click(await screen.findByRole("option", { name: "Use device settings", exact: true }));
    expect(values().firmware_transition_effect).toBe("device");
  });

  it("maps both direction labels to their actual values", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    for (const [label, value] of [["Reverse", "reverse"], ["Normal", "normal"], ["Use device settings", "device"]]) {
      await user.click(screen.getByRole("combobox", { name: "Firmware transition direction" }));
      await user.click(await screen.findByRole("option", { name: label, exact: true }));
      expect(values().firmware_transition_direction).toBe(value);
    }
  });

  it("preserves blank versus zero and explicit rotation off", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const duration = screen.getByLabelText("Firmware transition duration (ms)");
    expect(values()).not.toHaveProperty("firmware_transition_duration_ms");
    await user.type(duration, "0");
    expect(values().firmware_transition_duration_ms).toBe(0);
    await user.clear(duration);
    await user.tab();
    expect(values()).not.toHaveProperty("firmware_transition_duration_ms");
    await user.click(screen.getByRole("combobox", { name: "Automatic firmware rotation" }));
    await user.click(await screen.findByRole("option", { name: "Off", exact: true }));
    expect(values().firmware_auto_rotation).toBe("off");
    expect(screen.getByRole("switch", { name: "Override firmware transitions" })).not.toBeChecked();
  });
});
