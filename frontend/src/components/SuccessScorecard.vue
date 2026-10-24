<template>
  <div v-if="evaluation" class="scorecard">
    <div class="scorecard-head">
      <div class="score-block">
        <span class="score-label">{{ $t('scorecard.successScore') }}</span>
        <span class="score-value">{{ fmt(card.success_score) }}<small>/100</small></span>
      </div>
      <div class="verdict-block">
        <span class="verdict" :class="`verdict--${card.verdict?.code || 'none'}`">{{ card.verdict?.label || '-' }}</span>
        <span class="confidence">{{ $t('scorecard.confidence') }}: {{ card.confidence?.label || '-' }}</span>
      </div>
    </div>

    <div class="component-list">
      <div v-for="(component, key) in card.components" :key="key" class="component-row">
        <span class="component-label">{{ component.label }}</span>
        <div class="bar"><div class="bar-fill" :style="{ width: `${component.score ?? 0}%` }"></div></div>
        <span class="component-score">{{ fmt(component.score) }}</span>
      </div>
    </div>

    <div class="metric-grid">
      <div class="metric"><span>{{ $t('scorecard.respondents') }}</span><b>{{ fmt(metrics.respondents) }} / {{ fmt(metrics.surveyed) }}</b></div>
      <div class="metric"><span>{{ $t('scorecard.top2box') }}</span><b>{{ fmt(metrics.top2box_share, '%') }}</b></div>
      <div class="metric"><span>NPS</span><b>{{ fmt(metrics.nps) }}</b></div>
      <div class="metric"><span>{{ $t('scorecard.medianWtp') }}</span><b>{{ fmt(metrics.median_willingness_to_pay) }}</b></div>
      <div class="metric"><span>{{ $t('scorecard.priceFit') }}</span><b>{{ fmt(metrics.price_fit_share, '%') }}</b></div>
      <div class="metric"><span>{{ $t('scorecard.conversion') }}</span><b>{{ fmt(metrics.estimated_conversion_adjusted, '%') }}</b></div>
    </div>

    <div v-for="group in insightGroups" :key="group.key" class="insight-group">
      <h4>{{ $t(`scorecard.${group.key}`) }}</h4>
      <ul>
        <li v-for="(item, idx) in group.items" :key="idx">{{ item }}</li>
      </ul>
    </div>

    <p class="disclaimer">{{ $t('scorecard.disclaimer') }}</p>
  </div>
</template>

<script setup>
import { ref, computed, watch, onUnmounted } from 'vue'
import { getSuccessEvaluation } from '../api/report'

const props = defineProps({
  simulationId: String
})

const POLL_INTERVAL_MS = 10000
const INSIGHT_KEYS = ['success_drivers', 'key_risks', 'recommendations', 'support_programs', 'regulatory_checks']

const evaluation = ref(null)
let timer = null

const card = computed(() => evaluation.value?.scorecard || {})
const metrics = computed(() => card.value.metrics || {})
const insightGroups = computed(() => INSIGHT_KEYS
  .map(key => ({ key, items: evaluation.value?.insights?.[key] || [] }))
  .filter(group => group.items.length > 0))

const fmt = (value, suffix = '') => {
  if (value === null || value === undefined) return '-'
  return `${typeof value === 'number' ? value.toLocaleString() : value}${suffix}`
}

const stopPolling = () => {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

// 평가는 보고서 생성 첫 단계에서 만들어지므로 결과가 생길 때까지 조회한다
const load = async () => {
  if (!props.simulationId) return
  try {
    const res = await getSuccessEvaluation(props.simulationId)
    if (res.success && res.data) {
      evaluation.value = res.data
      stopPolling()
    }
  } catch (e) {
    // 404: 아직 평가 전
  }
}

watch(() => props.simulationId, (id) => {
  stopPolling()
  evaluation.value = null
  if (!id) return
  load()
  timer = setInterval(load, POLL_INTERVAL_MS)
}, { immediate: true })

onUnmounted(stopPolling)
</script>

<style scoped>
.scorecard {
  border: 1px solid #000;
  padding: 20px;
  margin: 0 0 32px;
  background: #FFF;
}

.scorecard-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}

.score-block {
  display: flex;
  flex-direction: column;
}

.score-label,
.confidence {
  font-size: 0.75rem;
  color: #666;
}

.score-value {
  font-size: 2.5rem;
  font-weight: 700;
  line-height: 1;
}

.score-value small {
  font-size: 1rem;
  color: #999;
  margin-left: 4px;
}

.verdict-block {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 4px;
}

.verdict {
  padding: 4px 10px;
  font-weight: 600;
  font-size: 0.9rem;
  color: #FFF;
  background: #666;
}

.verdict--go { background: #1A7F37; }
.verdict--conditional_go { background: #2F6FEB; }
.verdict--pivot { background: #BF8700; }
.verdict--no_go { background: #CF222E; }

.component-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 16px;
}

.component-row {
  display: grid;
  grid-template-columns: minmax(120px, 1.2fr) 2fr 48px;
  align-items: center;
  gap: 12px;
  font-size: 0.85rem;
}

.bar {
  height: 8px;
  background: #EEE;
}

.bar-fill {
  height: 100%;
  background: #000;
}

.component-score {
  text-align: right;
  font-weight: 600;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 16px;
}

.metric {
  display: flex;
  flex-direction: column;
  padding: 8px;
  background: #F5F5F5;
  font-size: 0.75rem;
  color: #666;
}

.metric b {
  font-size: 1rem;
  color: #000;
}

.insight-group h4 {
  margin: 12px 0 4px;
  font-size: 0.9rem;
}

.insight-group ul {
  margin: 0;
  padding-left: 18px;
  font-size: 0.85rem;
  line-height: 1.6;
}

.disclaimer {
  margin: 16px 0 0;
  font-size: 0.75rem;
  color: #888;
}

@media (max-width: 640px) {
  .metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .component-row {
    grid-template-columns: 1fr 48px;
  }

  .component-row .bar {
    grid-column: 1 / -1;
    grid-row: 2;
  }
}
</style>
