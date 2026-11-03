import { bindings, defineConfig, triggers } from "cf/config";
import * as entrypoint from "./src/index.ts" with { type: "cf-worker" };

// Cron triggers run in UTC. Each ET job has an EDT and an EST variant; the Worker
// checks the New York clock and ignores whichever one is off by an hour.
export default defineConfig({
	worker: {
		name: "moola-scheduler",
		compatibilityDate: "2026-10-06",
		entrypoint,
		observability: { enabled: true },
		workersDev: false, // cron only: no public URL
		previewUrls: false,
		env: {
			GITHUB_REPO: bindings.text("zkp6c4vr89-coder/Moola"),
			GITHUB_TOKEN: bindings.secret(), // fine-grained PAT, this repo only, Actions: read & write
		},
		triggers: [
			triggers.scheduled({ schedule: "35 13,14 * * 1-5" }), // daybot morning: 09:35 ET
			triggers.scheduled({ schedule: "35 16,17 * * 1-5" }), // daybot afternoon: 12:35 ET
			triggers.scheduled({ schedule: "30 21 * * *" }), // Moola daily run, after the US close
		],
	},
});
