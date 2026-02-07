import adapter from '@sveltejs/adapter-static';

const config = {
  kit: {
    adapter: adapter({
      fallback: '200.html'
    }),
    paths: {
      base: process.env.PUBLIC_BASE_PATH ?? ''
    },
    alias: {
			components: 'src/components',
			types: 'src/types',
			utils: 'src/utils',
			sounds: './static/sounds',
			'course-client': 'src/course-client'
		}
  }
};

export default config;

