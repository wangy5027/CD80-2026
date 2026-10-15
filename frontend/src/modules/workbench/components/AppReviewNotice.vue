<script setup>
defineProps({ status: { type: String, default: 'unverified' } });
</script>

<template>
  <section v-if="!status || status === 'unverified'" class="app-review-notice" role="status" aria-label="应用验证提醒">
    <p>您的应用创建成功，平台会在24小时之内对该应用进行验证审核，请注意查收邮箱</p>
  </section>
</template>

<style scoped>
.app-review-notice{margin:16px 0;padding:16px 18px;border:1px solid var(--line,#e4dcef);border-radius:12px;background:var(--surface-2,#f8f5fc);color:var(--text,#172033);overflow-wrap:anywhere}
.app-review-notice p{margin:0;font-size:14px;line-height:1.85}
</style>
