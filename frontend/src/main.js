import { createApp } from 'vue';
import Workbench from './modules/workbench/App.vue';
import './modules/workbench/pages/format.js';
import './modules/workbench/pages/styles.css';

createApp(Workbench).mount('#app');
