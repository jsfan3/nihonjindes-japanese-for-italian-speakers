<script lang="ts">
	import { locale } from 'svelte-i18n';
	import SkillCard from 'components/SkillCard/SkillCard.svelte';
	import NavBar from 'components/NavBar.svelte';

	import Column from 'components/Column.svelte';
	import Columns from 'components/Columns.svelte';
	import Content from 'components/Content.svelte';
	import Footer from 'components/DeprecatedFooter.svelte';
	import type { ModulesType } from 'types/ModulesType';
	import { page } from '$app/state';

	export const courseName = page.data.course.courseName;
	export let modules: ModulesType = page.data.course.modules;
	export let languageName = page.data.course.languageName;
	export const repositoryURL = page.data.course.repositoryURL;
	export let uiLanguage = 'es';
	const gistId = page.url.searchParams.get('gistId');
	locale.set(uiLanguage);
</script>

<svelte:head>
	<title>Nihonjindes - Esercizi di giapponese per italiani</title>
</svelte:head>

<NavBar hasAuth {repositoryURL} />

{#each modules as { title, skills }}
	<section class="section">
		<div class="container">
			<h2 class="is-size-2">{title}</h2>
			<Columns multiline>
				{#each skills as skill}
					<Column sizeDesktop="1/3" sizeTablet="1/2">
						<SkillCard
							{...{ ...skill }}
							practiceHref={`/course/${courseName}/skill/${skill.practiceHref}`}
							gistId={gistId}
						/>
					</Column>
				{/each}
			</Columns>
		</div>
	</section>
{/each}

<Footer>
	<Content>
		<Columns>
			<Column>
				<strong>Nihonjindes</strong>
				by <a href="https://www.informatica-libera.net/">Francesco Galgani</a> e basato su un 
				<a href="https://github.com/jsfan3/nihonjindes-japanese-for-italian-speakers?tab=readme-ov-file">fork di LibreLingo</a>. Il contenuto del corso (testo + immagini) segue il mio percorso personale di studio, e lo sto preparando e aggiornando via via. E' in una fase preliminare di preparazione.
			</Column>
			<Column>
				Il codice sorgente ha licenza
				<a href="https://opensource.org/licenses/AGPL-3.0">AGPL-3.0.</a><br />
                I contenuti del corso hanno licenza <a href="https://creativecommons.org/licenses/by-sa/4.0/">CC BY-SA 4.0 International</a>.
			</Column>
            <Column>
            Un ringraziamento particolare a <a href="https://www.nipponita.com/it/">Michela Viera (NipponITA)</a>.
			</Column>
		</Columns>
	</Content>
</Footer>

<style type="text/scss">
	.container {
		padding-right: 20px;
		padding-left: 20px;
	}
</style>
