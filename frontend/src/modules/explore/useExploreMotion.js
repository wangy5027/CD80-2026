import { computed, onMounted, onBeforeUnmount, ref } from 'vue';

const preferenceKey = 'ficagent.explore.motionEnabled';

function savedPreference() {
  if (typeof window === 'undefined') return true;
  try { return window.localStorage?.getItem(preferenceKey) !== 'false'; }
  catch { return true; }
}

// The user setting governs decoration only. System and visibility pauses do not
// overwrite it, so returning to the page resumes the user's chosen mode.
export function useExploreMotion() {
  const enabled = ref(savedPreference());
  const reducedMotion = ref(false);
  const pageHidden = ref(false);
  let media;

  const effectivePaused = computed(() => !enabled.value || reducedMotion.value || pageHidden.value);
  const setEnabled = value => {
    enabled.value = Boolean(value);
    try { window.localStorage?.setItem(preferenceKey, String(enabled.value)); }
    catch { /* Private or blocked storage must not prevent changing the page. */ }
  };
  const updateVisibility = () => { pageHidden.value = document.hidden; };
  const updateMedia = event => { reducedMotion.value = event.matches; };
  const hidePage = () => { pageHidden.value = true; };
  const showPage = () => { updateVisibility(); };

  onMounted(() => {
    media = window.matchMedia?.('(prefers-reduced-motion: reduce)');
    reducedMotion.value = Boolean(media?.matches);
    updateVisibility();
    if (media?.addEventListener) media.addEventListener('change', updateMedia);
    else media?.addListener?.(updateMedia);
    document.addEventListener('visibilitychange', updateVisibility);
    window.addEventListener('pagehide', hidePage);
    window.addEventListener('pageshow', showPage);
  });

  onBeforeUnmount(() => {
    if (media?.removeEventListener) media.removeEventListener('change', updateMedia);
    else media?.removeListener?.(updateMedia);
    document.removeEventListener('visibilitychange', updateVisibility);
    window.removeEventListener('pagehide', hidePage);
    window.removeEventListener('pageshow', showPage);
  });

  return { enabled, setEnabled, reducedMotion, effectivePaused };
}
