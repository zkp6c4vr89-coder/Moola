// Starts Moola's GitHub Actions workflows on time. GitHub's own cron can run hours late,
// which is fine for a daily bot but fatal for an intraday one; manual dispatches start in about a minute.
import { env } from "cloudflare:workers";

type Dispatch = { workflow: string; inputs: Record<string, string> };

function newYorkTime(at: Date): { hour: number; minute: number } {
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
	if (cron === "30 21 * * *") {
		return { workflow: "daily.yml", inputs: { dry_run: "false" } };
	}
	const { hour } = newYorkTime(at);
	if (cron === "35 13,14 * * 1-5" && hour === 9) {
		return { workflow: "daybot.yml", inputs: { session: "morning", dry_run: "auto" } };
	}
	if (cron === "35 16,17 * * 1-5" && hour === 12) {
		return { workflow: "daybot.yml", inputs: { session: "afternoon", dry_run: "auto" } };
	}
	return null; // the daylight-saving twin of a trigger that already ran
}

async function dispatch(d: Dispatch): Promise<void> {
	const url = `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${d.workflow}/dispatches`;
	const res = await fetch(url, {
		method: "POST",
		headers: {
			Authorization: `Bearer ${env.GITHUB_TOKEN}`,
			Accept: "application/vnd.github+json",
			"X-GitHub-Api-Version": "2022-11-28",
			"User-Agent": "moola-scheduler",
		},
		body: JSON.stringify({ ref: "main", inputs: d.inputs }),
	});
	if (res.status !== 204) {
		throw new Error(`dispatch ${d.workflow} failed: ${res.status} ${await res.text()}`);
	}
	console.log(`dispatched ${d.workflow}`, d.inputs);
}

export default {
	async scheduled(controller) {
		const d = plan(controller.cron, new Date(controller.scheduledTime));
		if (!d) {
			console.log(`skip ${controller.cron}: not the right New York hour`);
			return;
		}
		await dispatch(d);
	},
	fetch() {
		return new Response("moola-scheduler: cron only", { status: 404 });
	},
} satisfies ExportedHandler;
