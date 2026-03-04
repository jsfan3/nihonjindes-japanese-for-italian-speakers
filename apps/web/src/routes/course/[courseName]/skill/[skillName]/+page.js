import { building } from '$app/environment';
import { get_skill_data } from 'course-client';

export async function load({ params, url }) {
	const { skillName, courseName } = params;
	const gistId = building ? null : url.searchParams.get('gistId');

	if (courseName === 'preview') {
		const skillNameFromQuery = building ? null : url.searchParams.get('skillName');
		return {
			loading: true,
			gistId,
			preview: {
				type: skillName,
				gistId,
				skillName: skillNameFromQuery
			}
		};
	}

	return {
		...(await get_skill_data({ skillName, courseName, gistId })),
		loading: false,
		preview: null,
		gistId
	};
}
