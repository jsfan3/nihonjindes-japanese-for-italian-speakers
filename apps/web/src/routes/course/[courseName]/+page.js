import { building } from '$app/environment';

export async function load({ params, url }) {
	const { get_course } = await import('../../../course-client');
	const { courseName } = params;
	const gistId = building ? null : url.searchParams.get('gistId');
	const course = await get_course({ courseName, gistId });

	return { course, gistId };
}
