// Which workflow (if any) a cron fire should start. Pure, so it can be tested outside the Workers runtime.
//
// Cloudflare crons run in UTC and New York shifts between UTC-5 (EST) and UTC-4 (EDT), so each
// ET job has two UTC fires an hour apart. Only the one that lands on the right New York hour
// dispatches; its twin returns null and does nothing.

export type Dispatch = { workflow: string; inputs: Record<string, string> };

export const CRON_DAYBOT_MORNING = "35 13,14 * * 1-5"; // 09:35 ET: 13:35 UTC in EDT, 14:35 UTC in EST
export const CRON_DAYBOT_AFTERNOON = "35 16,17 * * 1-5"; // 12:35 ET: 16:35 UTC in EDT, 17:35 UTC in EST
export const CRON_MOOLA_DAILY = "30 21 * * *"; // 21:30 UTC daily, no ET gate (after the close in both)

export function newYorkTime(at: Date): { hour: number; minute: number } {
	const parts = new Intl.DateTimeFormat("en-US", {
		timeZone: "America/New_York",
		hour: "numeric",
		minute: "numeric",
		hourCycle: "h23",
	}).formatToParts(at);
	const get = (t: string) => Number(parts.find((p) => p.type === t)?.value);
	return { hour: get("hour"), minute: get("minute") };
}

export function plan(cron: string, at: Date): Dispatch | null {
	if (cron === CRON_MOOLA_DAILY) {
		return { workflow: "daily.yml", inputs: { dry_run: "false" } };
	}
	const ny = newYorkTime(at);
	if (cron === CRON_DAYBOT_MORNING && ny.hour === 9 && ny.minute === 35) {
		return { workflow: "daybot.yml", inputs: { session: "morning", dry_run: "auto" } };
	}
	if (cron === CRON_DAYBOT_AFTERNOON && ny.hour === 12 && ny.minute === 35) {
		return { workflow: "daybot.yml", inputs: { session: "afternoon", dry_run: "auto" } };
	}
	return null; // the daylight-saving twin of a fire that already ran, or an unknown cron
}
