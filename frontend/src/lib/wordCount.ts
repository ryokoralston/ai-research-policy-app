/** Count words in a text string. */
export function countWords(text: string): number {
  return text.trim() ? text.trim().split(/\s+/).length : 0;
}

/** Extract a target word-count range from instruction text, e.g. "150-250 words". */
export function parseWordRange(instructions: string): { min: number; max: number } | null {
  const range = instructions.match(/(\d+)\s*[-–]\s*(\d+)\s*words?/i);
  if (range) return { min: parseInt(range[1]), max: parseInt(range[2]) };
  const single = instructions.match(/(?:in|under|within|about)\s+(\d+)\s*words?/i);
  if (single) {
    const n = parseInt(single[1]);
    return { min: Math.round(n * 0.85), max: n };
  }
  return null;
}

/** Color class based on current count vs target range. */
export function wordCountColor(count: number, range: { min: number; max: number } | null): string {
  if (!range || count === 0) return "text-slate-500";
  if (count >= range.min && count <= range.max) return "text-green-400";
  if (count > range.max * 1.25 || count < range.min * 0.5) return "text-red-400";
  return "text-amber-400";
}
