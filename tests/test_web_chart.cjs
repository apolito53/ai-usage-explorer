const assert = require("node:assert/strict");
const { test } = require("node:test");
const { filteredCost, chartValues, chartScale, headerTotals } = require("../assets/usage-graphs/chart.js");

const rows = [
  { date: "2024-03-01", total: 5, models: { opus: 3, sonnet: 2 } },
  { date: "2024-03-02", total: 0, models: {} },
  { date: "2024-03-03", total: 7, models: { sonnet: 4, "": 3 } },
];

test("all, none, isolate and multiple models preserve the selection contract", () => {
  assert.deepEqual(chartValues(rows, null, false), [5, 0, 7]);
  assert.deepEqual(chartValues(rows, new Set(), false), [0, 0, 0]);
  assert.deepEqual(chartValues(rows, new Set(["sonnet"]), false), [2, 0, 4]);
  assert.deepEqual(chartValues(rows, new Set(["opus", ""]), false), [3, 0, 3]);
  assert.equal(filteredCost(rows[2], new Set([""])), 3);
});

test("cumulative values stay flat while idle and respect model filters", () => {
  assert.deepEqual(chartValues(rows, null, true), [5, 5, 12]);
  assert.deepEqual(chartValues(rows, new Set(["sonnet"]), true), [2, 2, 6]);
  assert.deepEqual(chartValues([], null, true), []);
});

test("headers follow month selection and filters without changing today's date", () => {
  const usage = { today: "2024-03-03", monthTotal: 12.5, todayTotal: 7,
    months: { "2024-03": rows, "2024-02": [{ total: 20, models: { opus: 20 } }] } };
  assert.deepEqual(headerTotals(usage, "2024-03", null), { month: 12.5, today: 7 });
  assert.deepEqual(headerTotals(usage, "2024-02", null), { month: 20, today: 7 });
  assert.deepEqual(headerTotals(usage, "2024-03", new Set(["sonnet"])), { month: 6, today: 4 });
  assert.deepEqual(headerTotals(usage, "2024-03", new Set()), { month: 0, today: 0 });
});

test("axis scaling handles empty, tiny and large spending totals", () => {
  assert.equal(chartScale([]), .01);
  for (const n of [0.0000001, 0.01, 1.7, 50000]) assert.ok(chartScale([n]) >= n);
});
