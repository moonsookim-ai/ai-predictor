// AGPL-3.0 13조: 웹으로 쓰는 사람에게 지금 돌고 있는 버전의 소스를 받을 길을 알린다.
// Docker 이미지는 빌드한 커밋을 VITE_SOURCE_COMMIT 으로 받아 그 커밋의 소스를 가리킨다.
export const SOURCE_REPO = 'https://github.com/moonsookim-ai/ai-predictor'

const commit = (import.meta.env.VITE_SOURCE_COMMIT || '').trim()

export const SOURCE_COMMIT = /^[0-9a-f]{7,40}$/.test(commit) ? commit : ''
export const SOURCE_URL = SOURCE_COMMIT ? `${SOURCE_REPO}/tree/${SOURCE_COMMIT}` : SOURCE_REPO
