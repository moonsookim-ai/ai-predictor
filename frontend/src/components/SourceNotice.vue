<template>
  <a class="source-notice" :href="SOURCE_URL" target="_blank" rel="noopener">
    {{ $t('source.label') }}<span v-if="SOURCE_COMMIT"> · {{ SOURCE_COMMIT.slice(0, 7) }}</span>
  </a>
</template>

<script setup>
import { SOURCE_COMMIT, SOURCE_URL } from '../source.js'
</script>

<style scoped>
.source-notice {
  position: fixed;
  right: 12px;
  bottom: 8px;
  z-index: 50;
  font-size: 11px;
  color: #666;
  background: rgba(255, 255, 255, 0.9);
  padding: 2px 6px;
  text-decoration: none;
}

.source-notice:hover {
  color: #000;
  text-decoration: underline;
}
</style>
