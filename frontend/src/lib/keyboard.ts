import type { KeyboardEvent } from "react";

/**
 * Check if a keyboard event is Enter being pressed outside of IME composition.
 *
 * While an IME (Input Method Editor) is active for CJK input (Japanese, Chinese, Korean),
 * pressing Enter commits the composition candidate. This guard ensures that we only
 * trigger form submission on the final, confirmed Enter keypress, not on the intermediate
 * one that confirms the IME candidate.
 */
export function isSubmitEnter(e: KeyboardEvent): boolean {
  return e.key === "Enter" && !e.nativeEvent.isComposing;
}
