// Starts Moola's GitHub Actions workflows on time. GitHub's own cron can run hours late,
// which is fine for a daily bot but fatal for an intraday one; manual dispatches start in about a minute.
import { env } from "cloudflare:workers";
import { type Dispatch, plan } from "./plan.ts";

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
			console.log(`skip ${controller.cron}: not 09:35/12:35 in New York (daylight-saving twin)`);
			return;
		}
		await dispatch(d);
	},
	fetch() {
		return new Response("moola-scheduler: cron only", { status: 404 });
	},
} satisfies ExportedHandler;
