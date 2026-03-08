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
	const gistId = page.data.gistId;
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
				Mi chiamo <a href="https://www.informatica-libera.net/">Francesco Galgani</a>.
                Sto realizzando <strong>Nihonjindes</strong>,
				un raccolta di esercizi per lo studio della lingua giapponese, partendo da un 
				<a href="https://github.com/jsfan3/nihonjindes-japanese-for-italian-speakers?tab=readme-ov-file">fork
                personalizzato di LibreLingo Community</a>. I contenuti (testo, audio e immagini) seguono il mio percorso
                studio, e li aggiorno via via. Se noti errori o imprecisioni, puoi <a href="https://www.informatica-libera.net/content/contacts">segnalarmeli</a>.
			</Column>
			<Column>
				Il codice sorgente di FreeLingo ha licenza
				<a href="https://opensource.org/licenses/AGPL-3.0">AGPL-3.0.</a><br />
                I contenuti del corso hanno licenza <a href="https://creativecommons.org/licenses/by-sa/4.0/">
                CC BY-SA 4.0 International</a>.
                La voce giapponese è di
                <a href="https://hub.aivis-project.com/aivm-models/59f96896-64d2-4378-830a-4d5feb3d81aa">Honoka</a>, con licenza <a href="https://github.com/Aivis-Project/ACML">ACML</a>. 
			</Column>
            <Column>
            Se stai cercando un insegnante di lingua giapponese, ti segnalo <a href="https://www.nipponita.com/it/">Michela Viera (NipponITA)</a>, che è ben organizzata e con un valido approccio pedagogico.
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
