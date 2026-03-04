import { building } from '$app/environment';
import { get_skill_introduction } from 'course-client';

export async function load({ params, url }) {
	const { courseName, skillName } = params;

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
		...(await get_skill_introduction({ courseName, skillName, gistId })),
		loading: false,
		preview: null,
		gistId
	};
}
