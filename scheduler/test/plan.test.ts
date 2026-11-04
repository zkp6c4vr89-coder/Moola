// Run with: npm test   (node's built-in test runner; no extra dependencies)
import assert from "node:assert/strict";
import { test } from "node:test";
import { CRON_DAYBOT_AFTERNOON, CRON_DAYBOT_MORNING, CRON_MOOLA_DAILY, plan } from "../src/plan.ts";

const fire = (cron: string, utc: string) => plan(cron, new Date(utc));
const session = (cron: string, utc: string) => fire(cron, utc)?.inputs.session ?? null;

// Each case: a weekday in EST and in EDT, both UTC fires of each ET cron.
// 2026 DST: EDT from Sun 8 Mar to Sun 1 Nov.
const cases = [
	{ zone: "EST (Wed 4 Nov 2026)", day: "2026-11-04", morning: "14:35", afternoon: "17:35", twinMorning: "13:35", twinAfternoon: "16:35" },
	{ zone: "EDT (Tue 7 Jul 2026)", day: "2026-07-07", morning: "13:35", afternoon: "16:35", twinMorning: "14:35", twinAfternoon: "17:35" },
	{ zone: "EST, first weekday after DST ends (Mon 2 Nov 2026)", day: "2026-11-02", morning: "14:35", afternoon: "17:35", twinMorning: "13:35", twinAfternoon: "16:35" },
	{ zone: "EDT, first weekday after DST starts (Mon 9 Mar 2026)", day: "2026-03-09", morning: "13:35", afternoon: "16:35", twinMorning: "14:35", twinAfternoon: "17:35" },
];

for (const c of cases) {
	test(`${c.zone}: the 09:35/12:35 ET fires dispatch daybot`, () => {
		assert.equal(session(CRON_DAYBOT_MORNING, `${c.day}T${c.morning}:00Z`), "morning");
		assert.equal(session(CRON_DAYBOT_AFTERNOON, `${c.day}T${c.afternoon}:00Z`), "afternoon");
		assert.equal(fire(CRON_DAYBOT_MORNING, `${c.day}T${c.morning}:00Z`)?.inputs.dry_run, "auto");
	});

	test(`${c.zone}: the extra UTC fires do nothing`, () => {
		assert.equal(fire(CRON_DAYBOT_MORNING, `${c.day}T${c.twinMorning}:00Z`), null);
		assert.equal(fire(CRON_DAYBOT_AFTERNOON, `${c.day}T${c.twinAfternoon}:00Z`), null);
	});
}

test("Moola's daily run dispatches every day, in both EST and EDT", () => {
	for (const utc of ["2026-11-04T21:30:00Z", "2026-07-07T21:30:00Z", "2026-11-07T21:30:00Z"]) {
		assert.deepEqual(fire(CRON_MOOLA_DAILY, utc), { workflow: "daily.yml", inputs: { dry_run: "false" } });
	}
});

test("a cron the Worker doesn't know dispatches nothing", () => {
	assert.equal(fire("0 0 * * *", "2026-11-04T00:00:00Z"), null);
});
