import { describe, expect, it } from "vitest";
import { buildSearchQuery } from "./api";
import { ago, duration, shortPath } from "./format";

describe("format", () => {
  it("formats relative time", () => {
    const now = 1_000_000;
    expect(ago(null, now)).toBe("imported");
    expect(ago(now - 30, now)).toBe("just now");
    expect(ago(now - 90, now)).toBe("1m ago");
    expect(ago(now - 3 * 86400, now)).toBe("3d ago");
  });

  it("formats durations", () => {
    expect(duration(null)).toBe("");
    expect(duration(850)).toBe("850ms");
    expect(duration(2500)).toBe("2.5s");
    expect(duration(125_000)).toBe("2m5s");
  });

  it("shortens home paths", () => {
    expect(shortPath("/home/me/code", "/home/me")).toBe("~/code");
    expect(shortPath("/home/meme", "/home/me")).toBe("/home/meme");
  });
});

describe("api", () => {
  it("builds search query strings", () => {
    const qs = new URLSearchParams(buildSearchQuery({ q: "docker", failed: true, repo: "app", smart: true }));
    expect(qs.get("q")).toBe("docker");
    expect(qs.get("failed")).toBe("true");
    expect(qs.get("repo")).toBe("app");
    expect(qs.get("smart")).toBe("true");
    expect(qs.has("since")).toBe(false);
  });
});
