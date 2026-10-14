import assert from "node:assert/strict";
import { test } from "node:test";
import { formatCount, formatDate, formatInt } from "./stats.ts";

test("formatCount abbreviates thousands", () => {
  assert.equal(formatCount(950), "950");
  assert.equal(formatCount(4413), "4.4k");
  assert.equal(formatCount(4000), "4k");
  assert.equal(formatCount(12345), "12k");
});

test("formatInt groups digits and marks missing values", () => {
  assert.equal(formatInt(215675), "215,675");
  assert.equal(formatInt(0), "0");
  assert.equal(formatInt(undefined), "n/a");
  assert.equal(formatInt(null), "n/a");
});

test("formatDate renders ISO dates in UTC", () => {
  assert.equal(formatDate("2026-10-13"), "Oct 13, 2026");
  assert.equal(formatDate("2026-01-01"), "Jan 1, 2026");
  assert.equal(formatDate(undefined), "n/a");
  assert.equal(formatDate("not a date"), "not a date");
});
