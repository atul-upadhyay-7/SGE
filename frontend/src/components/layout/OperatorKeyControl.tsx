import { useState } from "react";

import { getOperatorKey, setOperatorKey } from "@/services/operatorKey";

/** Lets an operator supply the write key for this browser tab only. */
export function OperatorKeyControl() {
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  const [saved, setSaved] = useState(getOperatorKey() !== null);

  function save() {
    setOperatorKey(value);
    setSaved(value.trim() !== "");
    setValue("");
    setOpen(false);
  }

  return (
    <div className="operator-key">
      <button
        type="button"
        className="btn"
        aria-expanded={open}
        onClick={() => setOpen(current => !current)}
      >
        {saved ? "Operator key set" : "Operator key"}
      </button>
      {open && (
        <form
          className="operator-key__form"
          onSubmit={event => {
            event.preventDefault();
            save();
          }}
        >
          <label htmlFor="operator-key-input">Operator key (this tab only)</label>
          <input
            id="operator-key-input"
            type="password"
            autoComplete="off"
            value={value}
            onChange={event => setValue(event.target.value)}
          />
          <button type="submit" className="btn btn--primary">
            {value.trim() ? "Save" : "Clear"}
          </button>
        </form>
      )}
    </div>
  );
}
