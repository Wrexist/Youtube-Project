/**
 * Whether this browser has been through the welcome flow.
 *
 * A cookie rather than engine state, deliberately: "have I seen the intro" is a
 * property of the person at this browser, not of the install. Two people sharing
 * one Studio should not mark the tour as read for each other, and the engine has
 * no notion of who is asking.
 *
 * In its own module because `app/actions.ts` carries `"use server"`, and a
 * server-action module may export nothing but async functions — a plain `const`
 * there fails the build with "the requested export doesn't exist" pointing at
 * every file that transitively imports it, which names everything except the
 * cause. Neither `tsc` nor ESLint catches it; only `next build` does.
 */
export const ONBOARDED_COOKIE = "studio_onboarded";

/** A year. A session cookie would bring the tour back on every browser restart. */
export const ONBOARDED_MAX_AGE = 60 * 60 * 24 * 365;
