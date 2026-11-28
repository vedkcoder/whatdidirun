import { describe, expect, it } from "vitest";

// `useEffect(() => el.scrollIntoView())` returns whatever the call returns. Newer browsers return a Promise
// from scrollIntoView, React then calls it as the effect's cleanup and the whole page goes blank
// ("l is not a function"). Effects must use a block body unless they really return a cleanup function.
const files = import.meta.glob<string>(["./**/*.ts", "./**/*.tsx", "!./**/*.test.ts"], {
  query: "?raw",
  import: "default",
  eager: true,
});

describe("effects", () => {
  it("never return an expression from a useEffect arrow", () => {
    expect(Object.keys(files).length).toBeGreaterThan(3);
    const offenders = Object.entries(files).flatMap(([file, text]) =>
      text
        .split("\n")
        .map((line, i) => ({ line, at: `${file}:${i + 1}` }))
        .filter(({ line }) => /useEffect\(\s*\(\)\s*=>\s*[^{\s]/.test(line))
        .map(({ line, at }) => `${at}: ${line.trim()}`),
    );
    expect(offenders).toEqual([]);
  });
});
