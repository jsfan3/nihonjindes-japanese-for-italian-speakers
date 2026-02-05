import { redirect } from '@sveltejs/kit';
import { base } from '$app/paths';

export const load = () => {
  // ${base} is needed if we publish the web app in a subfolder in the future (e.g., /course).
  throw redirect(302, `${base}/course/japanese-from-italian`);
};

