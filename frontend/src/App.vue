<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const MAX_SIZE = 50 * 1024 * 1024
const MAX_AVATAR_SIZE = 10 * 1024 * 1024
const AUDIO_STORAGE_KEY = 'pptvoice:audio_job'
const VIDEO_STORAGE_PREFIX = 'pptvoice:video:'
const file = ref(null)
const voice = ref('female')
const speed = ref(1)
const dragging = ref(false)
const submitting = ref(false)
const job = ref(null)
const error = ref('')

const avatarFile = ref(null)
const avatarPreview = ref('')
const videoJob = ref(null)
const videoSubmitting = ref(false)
const videoError = ref('')

let pollTimer = null
let pollFailures = 0
let videoPollTimer = null
let videoPollFailures = 0

const voices = [
  { id: 'female', name: '清悦女声', note: '清晰自然，适合课程和汇报', avatar: '悦' },
  { id: 'male', name: '沉稳男声', note: '可靠有力，适合商务与演示', avatar: '沉' },
  { id: 'warm', name: '温暖旁白', note: '亲切舒缓，适合故事与讲解', avatar: '暖' },
]

const statusText = computed(() => ({
  queued: '等待处理',
  processing: '正在生成',
  completed: '生成完成',
  failed: '生成失败',
}[job.value?.status] || ''))

const statusHint = computed(() => ({
  queued: '任务已进入队列，正在等待处理',
  processing: '正在提取内容并合成语音，可能需要几分钟',
  completed: '口播文稿与音频均已生成',
  failed: '本次生成没有完成，可以调整参数后重试',
}[job.value?.status] || ''))

const activeJob = computed(() => ['queued', 'processing'].includes(job.value?.status))
const locked = computed(() => submitting.value || activeJob.value)
const canSubmit = computed(() => Boolean(file.value) && !locked.value && job.value?.status !== 'completed')

const statusStep = computed(() => {
  if (job.value?.status === 'completed') return 3
  if (job.value?.status === 'processing') return 2
  if (job.value?.status === 'queued') return 1
  return 0
})

const speedLabel = computed(() => `${Number(speed.value.toFixed(2))}×`)
const speedValue = computed(() => `${((speed.value - 0.75) / 0.5) * 100}%`)

const audioExtension = computed(() => {
  const match = job.value?.audio_name?.match(/\.[a-z0-9]+$/i)
  return match ? match[0].toLowerCase() : '.mp3'
})

const downloadName = computed(() => {
  const stem = (job.value?.filename || '口播文稿').replace(/\.pptx$/i, '') || '口播文稿'
  return `${stem}${audioExtension.value}`
})

const downloadLabel = computed(() =>
  audioExtension.value === '.mp3' ? '下载 MP3' : `下载 ${audioExtension.value.slice(1).toUpperCase()}`
)

const videoStatusText = computed(() => ({
  queued: '等待生成',
  processing: '正在合成视频',
  completed: '视频生成完成',
  failed: '视频生成失败',
}[videoJob.value?.status] || ''))

const videoStatusHint = computed(() => ({
  queued: '视频任务已排队，稍后开始合成',
  processing: `正在用 FFmpeg 合成 1280×720 视频，已完成 ${videoProgress.value}%`,
  completed: '数字人视频已生成，可直接播放或下载',
  failed: '本次视频生成没有完成，可以换张图片重试',
}[videoJob.value?.status] || ''))

const videoActive = computed(() => ['queued', 'processing'].includes(videoJob.value?.status))
const videoBusy = computed(() => videoSubmitting.value || videoActive.value)
const canCreateVideo = computed(() => Boolean(avatarFile.value)
  && job.value?.status === 'completed'
  && !videoBusy.value
  && videoJob.value?.status !== 'completed')

const videoProgress = computed(() => {
  const value = Number(videoJob.value?.progress || 0)
  return Math.min(100, Math.max(0, value))
})

const videoDownloadName = computed(() => {
  const stem = (job.value?.filename || '口播').replace(/\.pptx$/i, '') || '口播'
  return `${stem}-数字人.mp4`
})

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function stopPolling() {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

function videoStorageKey(audioJobId) {
  return `${VIDEO_STORAGE_PREFIX}${audioJobId}`
}

function stopVideoPolling() {
  if (videoPollTimer) {
    clearTimeout(videoPollTimer)
    videoPollTimer = null
  }
}

function clearAvatarPreview() {
  if (avatarPreview.value) {
    URL.revokeObjectURL(avatarPreview.value)
    avatarPreview.value = ''
  }
}

function resetVideoState({ clearStored = true } = {}) {
  stopVideoPolling()
  videoPollFailures = 0
  if (clearStored && job.value?.id) {
    localStorage.removeItem(videoStorageKey(job.value.id))
  }
  videoJob.value = null
  videoError.value = ''
}

function chooseAvatar(event) {
  const selected = event.target.files?.[0]
  event.target.value = ''
  setAvatar(selected)
}

function setAvatar(selected) {
  videoError.value = ''
  clearAvatarPreview()
  avatarFile.value = null
  if (!selected) return
  if (!/\.(png|jpe?g)$/i.test(selected.name)) {
    videoError.value = '请选择 PNG 或 JPG 格式的图片'
    return
  }
  if (selected.size > MAX_AVATAR_SIZE) {
    videoError.value = '图片不能超过 10 MB'
    return
  }
  if (selected.size === 0) {
    videoError.value = '图片内容为空，请重新选择'
    return
  }
  avatarFile.value = selected
  avatarPreview.value = URL.createObjectURL(selected)
}

function removeAvatar() {
  if (videoBusy.value) return
  clearAvatarPreview()
  avatarFile.value = null
  videoError.value = ''
}

function scheduleVideoPoll() {
  stopVideoPolling()
  videoPollTimer = setTimeout(pollVideo, 2000)
}

async function pollVideo() {
  const id = videoJob.value?.id
  if (!id) return
  try {
    const body = await readJson(await fetch(`/api/video-jobs/${id}`))
    videoJob.value = body
    videoPollFailures = 0
    if (body.status === 'completed' || body.status === 'failed') {
      stopVideoPolling()
      if (body.status === 'failed') {
        videoError.value = body.error || '视频生成失败，请稍后重试'
      }
      return
    }
    scheduleVideoPoll()
  } catch (reason) {
    videoPollFailures += 1
    if (videoPollFailures >= 3) {
      stopVideoPolling()
      videoError.value = friendlyError(reason, '查询视频状态失败，请刷新页面重试')
    } else {
      scheduleVideoPoll()
    }
  }
}

async function restoreVideoJob() {
  const audioId = job.value?.id
  if (!audioId) return
  const key = videoStorageKey(audioId)
  const videoId = localStorage.getItem(key)
  if (!videoId) return
  try {
    const body = await readJson(await fetch(`/api/video-jobs/${videoId}`))
    videoJob.value = body
    if (body.status === 'queued' || body.status === 'processing') {
      scheduleVideoPoll()
    } else if (body.status === 'failed') {
      videoError.value = body.error || '视频生成失败，请稍后重试'
    }
  } catch {
    localStorage.removeItem(key)
  }
}

async function createVideo() {
  if (!canCreateVideo.value) return
  videoError.value = ''
  videoSubmitting.value = true
  const payload = new FormData()
  payload.append('avatar', avatarFile.value)
  try {
    const body = await readJson(await fetch(`/api/jobs/${job.value.id}/videos`, {
      method: 'POST',
      body: payload,
    }))
    videoJob.value = body
    localStorage.setItem(videoStorageKey(job.value.id), body.id)
    videoPollFailures = 0
    scheduleVideoPoll()
  } catch (reason) {
    videoError.value = friendlyError(reason, '创建视频任务失败，请稍后重试')
  } finally {
    videoSubmitting.value = false
  }
}

function regenerateVideo() {
  resetVideoState()
}

async function restoreAudioJob() {
  const id = localStorage.getItem(AUDIO_STORAGE_KEY)
  if (!id) return
  try {
    const body = await readJson(await fetch(`/api/jobs/${id}`))
    job.value = body
    if (body.status === 'queued' || body.status === 'processing') {
      schedulePoll()
    } else if (body.status === 'completed') {
      restoreVideoJob()
    }
  } catch {
    localStorage.removeItem(AUDIO_STORAGE_KEY)
  }
}

function chooseFile(event) {
  const selected = event.target.files?.[0]
  event.target.value = ''
  setFile(selected)
}

function dropFile(event) {
  dragging.value = false
  if (locked.value) return
  setFile(event.dataTransfer?.files?.[0])
}

function setFile(selected) {
  error.value = ''
  stopPolling()
  pollFailures = 0
  resetVideoState()
  localStorage.removeItem(AUDIO_STORAGE_KEY)
  job.value = null
  if (!selected) return
  if (!selected.name.toLowerCase().endsWith('.pptx')) {
    error.value = '请选择 .pptx 格式的演示文稿'
    file.value = null
    return
  }
  if (selected.size > MAX_SIZE) {
    error.value = '文件不能超过 50 MB'
    file.value = null
    return
  }
  if (selected.size === 0) {
    error.value = '文件内容为空，请重新选择'
    file.value = null
    return
  }
  file.value = selected
}

function removeFile() {
  if (locked.value) return
  file.value = null
  error.value = ''
}

function errorMessage(status) {
  if (status === 413) return '文件超过 50 MB，请压缩后再试'
  if (status === 404) return '任务不存在或已失效'
  if (status === 502 || status === 503 || status === 504) return '服务暂时不可用，请稍后重试'
  return `请求失败（${status}）`
}

function friendlyError(reason, fallback) {
  if (reason instanceof TypeError) return '网络连接失败，请检查网络后重试'
  return reason?.message || fallback
}

async function readJson(response) {
  let body = null
  try {
    body = await response.json()
  } catch {
    body = null
  }
  if (!response.ok) {
    const detail = body && typeof body.detail === 'string' ? body.detail : ''
    throw new Error(detail || errorMessage(response.status))
  }
  return body
}

async function submit() {
  if (!canSubmit.value) return
  error.value = ''
  submitting.value = true
  resetVideoState()
  job.value = null
  const payload = new FormData()
  payload.append('file', file.value)
  payload.append('voice', voice.value)
  payload.append('speed', String(speed.value))
  try {
    job.value = await readJson(await fetch('/api/jobs', { method: 'POST', body: payload }))
    localStorage.setItem(AUDIO_STORAGE_KEY, job.value.id)
    pollFailures = 0
    schedulePoll()
  } catch (reason) {
    error.value = friendlyError(reason, '上传失败，请稍后重试')
  } finally {
    submitting.value = false
  }
}

function schedulePoll() {
  stopPolling()
  pollTimer = setTimeout(poll, 2000)
}

async function poll() {
  const id = job.value?.id
  if (!id) return
  try {
    const body = await readJson(await fetch(`/api/jobs/${id}`))
    job.value = body
    pollFailures = 0
    if (body.status === 'completed' || body.status === 'failed') {
      stopPolling()
      if (body.status === 'failed') error.value = body.error || '生成失败，请稍后重试'
      else if (!videoJob.value) restoreVideoJob()
      return
    }
    schedulePoll()
  } catch (reason) {
    pollFailures += 1
    if (pollFailures >= 3) {
      stopPolling()
      error.value = friendlyError(reason, '查询任务状态失败，请刷新页面重试')
    } else {
      schedulePoll()
    }
  }
}

function reset() {
  stopPolling()
  resetVideoState()
  localStorage.removeItem(AUDIO_STORAGE_KEY)
  pollFailures = 0
  file.value = null
  job.value = null
  error.value = ''
  clearAvatarPreview()
  avatarFile.value = null
}

onMounted(restoreAudioJob)

onBeforeUnmount(() => {
  stopPolling()
  stopVideoPolling()
  clearAvatarPreview()
})
</script>

<template>
  <div class="page-shell">
    <header class="topbar">
      <a class="brand" href="#" aria-label="声页首页">
        <span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
        <span>声页</span>
      </a>
      <span class="tagline">让每一页，都有声音</span>
    </header>

    <main>
      <section class="hero" aria-labelledby="page-title">
        <div class="eyebrow"><span aria-hidden="true"></span> PPT TO VOICE</div>
        <h1 id="page-title">把演示文稿，<br /><em>变成好听的口播。</em></h1>
        <p>你只管上传，剩下的交给AI!</p>
      </section>

      <section class="studio-card" aria-label="PPT 转口播工作台">
        <div class="step-block">
          <div class="step-heading">
            <span aria-hidden="true">01</span>
            <div>
              <h2 id="upload-heading">上传演示文稿</h2>
              <p id="upload-hint">支持 PPTX，单个文件最大 50 MB</p>
            </div>
          </div>

          <div class="upload-area">
            <label
              class="dropzone"
              for="ppt-file"
              :class="{ active: dragging, selected: file, disabled: locked }"
              :aria-disabled="locked"
              @dragover.prevent="dragging = !locked"
              @dragleave.prevent="dragging = false"
              @drop.prevent="dropFile"
            >
              <input
                id="ppt-file"
                class="visually-hidden"
                type="file"
                accept=".pptx,application/vnd.openxmlformats-officedocument.presentationml.presentation"
                :disabled="locked"
                aria-describedby="upload-hint"
                @change="chooseFile"
              />
              <template v-if="file">
                <span class="file-icon" aria-hidden="true">P</span>
                <span class="file-info">
                  <strong :title="file.name">{{ file.name }}</strong>
                  <small>{{ formatSize(file.size) }} · 已准备</small>
                </span>
              </template>
              <template v-else>
                <span class="upload-icon" aria-hidden="true">↥</span>
                <strong>拖放文件到这里</strong>
                <small>或者点击选择 PPTX 文件</small>
              </template>
            </label>
            <button
              v-if="file"
              type="button"
              class="remove"
              aria-label="移除已选择的文件"
              :disabled="locked"
              @click="removeFile"
            >×</button>
          </div>
        </div>

        <div class="divider" role="presentation"></div>

        <div class="step-block">
          <div class="step-heading">
            <span aria-hidden="true">02</span>
            <div>
              <h2 id="voice-heading">选择声音</h2>
              <p>找到最适合内容的表达</p>
            </div>
          </div>
          <div class="voice-grid" role="radiogroup" aria-labelledby="voice-heading" :aria-disabled="locked">
            <label
              v-for="item in voices"
              :key="item.id"
              class="voice-card"
              :class="{ chosen: voice === item.id, disabled: locked }"
            >
              <input
                v-model="voice"
                type="radio"
                name="voice"
                :value="item.id"
                :disabled="locked"
              />
              <span class="voice-avatar" aria-hidden="true">{{ item.avatar }}</span>
              <span class="voice-copy">
                <strong>{{ item.name }}</strong>
                <small>{{ item.note }}</small>
              </span>
              <i class="radio-dot" aria-hidden="true"></i>
            </label>
          </div>
        </div>

        <div class="divider" role="presentation"></div>

        <div class="step-block speed-block">
          <div class="step-heading">
            <span aria-hidden="true">03</span>
            <div>
              <h2 id="speed-heading">调整语速</h2>
              <p>从舒缓到明快，自由掌控节奏</p>
            </div>
          </div>
          <div class="speed-control">
            <span aria-hidden="true">慢</span>
            <input
              id="speed"
              v-model.number="speed"
              type="range"
              min="0.75"
              max="1.25"
              step="0.05"
              :style="{ '--value': speedValue }"
              :disabled="locked"
              aria-labelledby="speed-heading"
              aria-valuemin="0.75"
              aria-valuemax="1.25"
              :aria-valuenow="speed"
              :aria-valuetext="speedLabel"
            />
            <span aria-hidden="true">快</span>
            <output for="speed" aria-live="off">{{ speedLabel }}</output>
          </div>
        </div>

        <p v-if="error" class="alert" role="alert">{{ error }}</p>

        <button
          type="button"
          class="primary-button"
          :disabled="!canSubmit"
          :aria-busy="submitting || activeJob"
          @click="submit"
        >
          <span v-if="submitting">正在上传…</span>
          <span v-else-if="activeJob">{{ statusText }}<i class="loading-dots" aria-hidden="true">…</i></span>
          <span v-else-if="job?.status === 'failed'">重新生成口播 <b aria-hidden="true">→</b></span>
          <span v-else-if="job?.status === 'completed'">口播已生成</span>
          <span v-else>开始生成口播 <b aria-hidden="true">→</b></span>
        </button>

        <section
          v-if="job"
          class="result"
          :class="job.status"
          aria-labelledby="result-heading"
          :aria-busy="activeJob"
        >
          <div class="result-head" role="status" aria-live="polite" aria-atomic="true">
            <span class="result-state" aria-hidden="true">
              {{ job.status === 'completed' ? '✓' : job.status === 'failed' ? '!' : '◌' }}
            </span>
            <div>
              <strong id="result-heading">{{ statusText }}</strong>
              <small>{{ statusHint }}</small>
            </div>
          </div>

          <ol v-if="job.status !== 'failed'" class="status-track" aria-hidden="true">
            <li :class="{ done: statusStep > 1, current: statusStep === 1 }">排队</li>
            <li :class="{ done: statusStep > 2, current: statusStep === 2 }">生成</li>
            <li :class="{ done: statusStep >= 3 }">完成</li>
          </ol>

          <template v-if="job.status === 'completed'">
            <audio
              class="result-audio"
              controls
              preload="metadata"
              :src="job.audio_url"
              :aria-label="`${job.filename} 的口播音频`"
            >
              你的浏览器不支持音频播放，请下载音频文件。
            </audio>
            <div class="result-actions">
              <a class="download" :href="job.download_url" :download="downloadName">
                {{ downloadLabel }} <span aria-hidden="true">↓</span>
              </a>
              <button type="button" class="text-button" @click="reset">上传新的演示文稿</button>
            </div>
          </template>

          <template v-else-if="job.status === 'failed'">
            <div class="result-actions">
              <button type="button" class="text-button" @click="reset">重新选择文件</button>
            </div>
          </template>
        </section>

        <section
          v-if="job?.status === 'completed'"
          class="video-studio"
          aria-labelledby="video-heading"
        >
          <div class="step-heading">
            <span aria-hidden="true">04</span>
            <div>
              <h2 id="video-heading">生成数字人口播视频</h2>
              <p id="avatar-hint">上传一张数字人图片，与 AI 口播音频合成为静态口播 MP4</p>
            </div>
          </div>

          <div class="avatar-area">
            <label
              class="avatar-dropzone"
              for="avatar-file"
              :class="{ selected: avatarFile, disabled: videoBusy }"
              :aria-disabled="videoBusy"
            >
              <input
                id="avatar-file"
                class="visually-hidden"
                type="file"
                accept="image/png,image/jpeg,.png,.jpg,.jpeg"
                :disabled="videoBusy"
                aria-describedby="avatar-hint"
                @change="chooseAvatar"
              />
              <template v-if="avatarPreview">
                <img class="avatar-preview" :src="avatarPreview" alt="数字人图片预览" />
                <span class="avatar-copy">
                  <strong :title="avatarFile?.name">{{ avatarFile?.name }}</strong>
                  <small>{{ formatSize(avatarFile?.size || 0) }} · 已准备</small>
                </span>
              </template>
              <template v-else>
                <span class="upload-icon" aria-hidden="true">☺</span>
                <strong>上传静态数字人图片</strong>
                <small>PNG 或 JPG，最大 10 MB</small>
              </template>
            </label>
            <button
              v-if="avatarFile"
              type="button"
              class="remove"
              aria-label="移除数字人图片"
              :disabled="videoBusy"
              @click="removeAvatar"
            >×</button>
          </div>

          <button
            v-if="videoJob?.status !== 'completed'"
            type="button"
            class="primary-button video-button"
            :disabled="!canCreateVideo"
            :aria-busy="videoBusy"
            @click="createVideo"
          >
            <span v-if="videoSubmitting">正在提交…</span>
            <span v-else-if="videoActive">{{ videoStatusText }}<i class="loading-dots" aria-hidden="true">…</i></span>
            <span v-else-if="videoJob?.status === 'failed'">重新生成视频 <b aria-hidden="true">→</b></span>
            <span v-else>生成口播视频 <b aria-hidden="true">→</b></span>
          </button>

          <p v-if="videoError" class="alert" role="alert">{{ videoError }}</p>

          <div
            v-if="videoJob"
            class="video-result"
            :class="videoJob.status"
            role="status"
            aria-live="polite"
          >
            <div class="result-head">
              <span class="result-state" aria-hidden="true">
                {{ videoJob.status === 'completed' ? '✓' : videoJob.status === 'failed' ? '!' : '◌' }}
              </span>
              <div>
                <strong>{{ videoStatusText }}</strong>
                <small>{{ videoStatusHint }}</small>
              </div>
            </div>

            <div
              v-if="videoActive"
              class="video-progress"
              role="progressbar"
              aria-label="视频生成进度"
              aria-valuemin="0"
              aria-valuemax="100"
              :aria-valuenow="videoProgress"
            >
              <span :style="{ width: `${videoProgress}%` }"></span>
            </div>

            <template v-if="videoJob.status === 'completed'">
              <video
                class="video-player"
                controls
                preload="metadata"
                :src="videoJob.video_url"
                :aria-label="`${job.filename} 的数字人口播视频`"
              >
                你的浏览器不支持视频播放，请下载视频文件。
              </video>
              <div class="result-actions">
                <a class="download" :href="videoJob.download_url" :download="videoDownloadName">
                  下载 MP4 <span aria-hidden="true">↓</span>
                </a>
                <button type="button" class="text-button" @click="regenerateVideo">重新生成视频</button>
              </div>
            </template>
          </div>
        </section>

        <section
          v-if="job?.status === 'completed' && job.transcript"
          class="transcript"
          aria-labelledby="transcript-heading"
        >
          <h2 id="transcript-heading">口播文稿</h2>
          <p>{{ job.transcript }}</p>
        </section>
      </section>

      <section class="features" aria-label="产品特点">
        <div><span aria-hidden="true">01</span><h3>智能提炼</h3><p>理解演示内容，生成自然口语，而非机械朗读。</p></div>
        <div><span aria-hidden="true">02</span><h3>声音可选</h3><p>多种声音与表达风格，让内容拥有合适的个性。</p></div>
        <div><span aria-hidden="true">03</span><h3>随时下载</h3><p>完成后在线试听，并将高清音频保存到本地。</p></div>
      </section>
    </main>

    <footer><span>声页 PPT TO VOICE</span><small>由 AI 驱动的演示文稿口播工具</small></footer>
  </div>
</template>
