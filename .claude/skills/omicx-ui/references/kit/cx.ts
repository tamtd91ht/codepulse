/** Join class names, skipping falsy values. Replace with the project's `cn` / `clsx` if it has one. */
export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
