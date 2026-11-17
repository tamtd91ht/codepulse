/*
 * Fuzzy matching for the command palette (docs/specs/31 §21.1), blind to Vietnamese accents:
 * "giao" finds "Giao cho tôi", "chuyen review" finds "Chuyển sang Đang review", "gct" finds
 * "Giao cho tôi" (word initials). Every typed word must be found; a higher score ranks first.
 */

/** Lower-case, accents removed, đ → d. */
export function fold(s: string): string {
  return s.replace(/đ/g, "d").replace(/Đ/g, "D").normalize("NFD").replace(/\p{Mn}/gu, "").toLowerCase();
}

/** One query word against the folded text: 4 at a word start, 2 inside a word, 1 as initials /
 * a scattered subsequence starting at a word start; 0 when not found. */
function wordScore(word: string, text: string, words: string[]): number {
  const at = text.indexOf(word);
  if (at >= 0) return at === 0 || /[^a-z0-9]/.test(text[at - 1]) ? 4 : 2;
  // Initials: "gct" → "giao cho toi".
  if (word.length >= 2 && words.length >= word.length) {
    for (let i = 0; i + word.length <= words.length; i++) {
      if ([...word].every((c, j) => words[i + j]?.startsWith(c))) return 1;
    }
  }
  // Scattered letters starting at a word start: "chyn" → "chuyen".
  if (word.length >= 3) {
    for (const w of words) {
      if (w[0] !== word[0]) continue;
      let k = 0;
      for (const c of w) if (c === word[k]) k++;
      if (k >= word.length) return 1;
    }
  }
  return 0;
}

/** The score of `query` against a label (plus hidden keywords); -1 when it does not match. An
 * empty query matches everything with score 0. Hits in the label weigh more than in keywords. */
export function matchScore(query: string, label: string, keywords = ""): number {
  const q = fold(query).trim();
  if (!q) return 0;
  const main = fold(label);
  const extra = fold(keywords);
  const mainWords = main.split(/[^a-z0-9]+/).filter(Boolean);
  const extraWords = extra.split(/[^a-z0-9]+/).filter(Boolean);
  let total = 0;
  for (const word of q.split(/\s+/)) {
    const inMain = wordScore(word, main, mainWords);
    const inExtra = inMain ? 0 : wordScore(word, extra, extraWords);
    if (!inMain && !inExtra) return -1;
    total += inMain ? inMain * 2 : inExtra;
  }
  // A label that starts with the whole query ranks above one that only holds its words.
  if (main.startsWith(q)) total += 5;
  return total;
}
