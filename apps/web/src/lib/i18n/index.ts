import { browser } from '$app/environment';
import { register, init } from 'svelte-i18n';
const defaultLocale = 'en';

register('en', () => import('./translation/en.json'));
register('it', () => import('./translation/it.json'));

init({ fallbackLocale: 'en', initialLocale: 'it' });

