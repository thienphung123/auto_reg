import { useEffect, useState, useCallback } from 'react'
import { useParams } from 'react-router-dom'
import {
  Table,
  Button,
  Input,
  InputNumber,
  Select,
  Tag,
  Space,
  Modal,
  Form,
  message,
  Popconfirm,
  Dropdown,
  Typography,
  Alert,
  theme,
} from 'antd'
import type { MenuProps } from 'antd'
import {
  ReloadOutlined,
  CopyOutlined,
  LinkOutlined,
  PlusOutlined,
  DownloadOutlined,
  UploadOutlined,
  MoreOutlined,
  DeleteOutlined,
  SyncOutlined,
} from '@ant-design/icons'
import { ChatGPTRegistrationModeSwitch } from '@/components/ChatGPTRegistrationModeSwitch'
import { TaskLogPanel } from '@/components/TaskLogPanel'
import { usePersistentChatGPTRegistrationMode } from '@/hooks/usePersistentChatGPTRegistrationMode'
import { parseBooleanConfigValue } from '@/lib/configValueParsers'
import { buildChatGPTRegistrationRequestAdapter } from '@/lib/chatgptRegistrationRequestAdapter'
import { apiFetch, getToken } from '@/lib/utils'
import { normalizeExecutorForPlatform } from '@/lib/platformExecutorOptions'

const { Text } = Typography

const STATUS_COLORS: Record<string, string> = {
  registered: 'default',
  trial: 'success',
  subscribed: 'success',
  expired: 'warning',
  invalid: 'error',
}

function parseExtraJson(raw: string | undefined) {
  if (!raw) return {}
  try {
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

function normalizeAccount(account: any) {
  const extra = parseExtraJson(account.extra_json)
  const syncStatuses = extra.sync_statuses && typeof extra.sync_statuses === 'object' ? extra.sync_statuses : {}
  const cliproxySync = syncStatuses.cliproxyapi && typeof syncStatuses.cliproxyapi === 'object' ? syncStatuses.cliproxyapi : {}
  const chatgptLocal = extra.chatgpt_local && typeof extra.chatgpt_local === 'object' ? extra.chatgpt_local : {}
  return { ...account, extra, cliproxySync, chatgptLocal }
}

function formatSyncTime(value?: string) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

function formatCreatedAt(value?: string) {
  if (!value) return { date: '-', time: '' }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return { date: value, time: '' }
  }
  return {
    date: date.toLocaleDateString(),
    time: date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
  }
}

function authStateMeta(state?: string) {
  switch (state) {
    case 'access_token_valid':
      return { color: 'success', label: 'AT Valid' }
    case 'account_deactivated':
      return { color: 'error', label: 'Invalid' }
    case 'access_token_invalidated':
      return { color: 'error', label: 'AT Invalid' }
    case 'unauthorized':
      return { color: 'error', label: 'Unauthorized' }
    case 'missing_access_token':
      return { color: 'default', label: 'Missing AT' }
    case 'banned_like':
      return { color: 'error', label: 'Possibly Banned' }
    case 'probe_failed':
      return { color: 'warning', label: 'Probe Failed' }
    default:
      return { color: 'default', label: 'Not Probed' }
  }
}

function codexStateMeta(state?: string) {
  switch (state) {
    case 'usable':
      return { color: 'success', label: 'Usable' }
    case 'account_deactivated':
      return { color: 'error', label: 'Invalid' }
    case 'access_token_invalidated':
      return { color: 'error', label: 'AT Invalid' }
    case 'unauthorized':
      return { color: 'error', label: 'Unauthorized' }
    case 'payment_required':
      return { color: 'warning', label: 'Payment / Permission Required' }
    case 'quota_exhausted':
      return { color: 'warning', label: 'Quota Exhausted' }
    case 'skipped_auth_invalid':
      return { color: 'default', label: 'Not Checked' }
    case 'probe_failed':
      return { color: 'warning', label: 'Probe Failed' }
    default:
      return { color: 'default', label: 'Not Probed' }
  }
}

function planMeta(plan?: string) {
  switch ((plan || '').toLowerCase()) {
    case 'plus':
      return { color: 'success', label: 'Plus' }
    case 'team':
      return { color: 'processing', label: 'Team' }
    case 'enterprise':
      return { color: 'processing', label: 'Enterprise' }
    case 'pro':
      return { color: 'processing', label: 'Pro' }
    case 'free':
      return { color: 'default', label: 'Free' }
    default:
      return { color: 'default', label: 'Unknown' }
  }
}

function formatStructuredText(value?: string) {
  if (!value) return ''
  const trimmed = String(value).trim()
  if (!trimmed) return ''
  if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
    try {
      return JSON.stringify(JSON.parse(trimmed), null, 2)
    } catch {
      return trimmed
    }
  }
  return trimmed
}

function SummaryField({
  label,
  value,
  code = false,
}: {
  label: string
  value?: string
  code?: boolean
}) {
  const { token } = theme.useToken()
  if (!value) return null

  const content = code ? formatStructuredText(value) : value
  const isBlock = code || content.length > 96 || content.includes('\n')

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '104px minmax(0, 1fr)',
        gap: 12,
        alignItems: 'start',
      }}
    >
      <Text type="secondary" style={{ fontSize: 12, lineHeight: '20px' }}>
        {label}
      </Text>
      {isBlock ? (
        <pre
          style={{
            margin: 0,
            padding: code ? '8px 10px' : 0,
            borderRadius: code ? token.borderRadius : 0,
            border: code ? `1px solid ${token.colorBorder}` : 'none',
            background: code ? token.colorBgElevated : 'transparent',
            color: code ? token.colorText : token.colorTextSecondary,
            fontFamily: code ? 'SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace' : 'inherit',
            fontSize: 12,
            lineHeight: 1.6,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            overflowWrap: 'anywhere',
            maxHeight: code ? 160 : 'none',
            overflow: code ? 'auto' : 'visible',
          }}
        >
          {content}
        </pre>
      ) : (
        <Text style={{ display: 'block', color: token.colorTextSecondary, lineHeight: '20px' }}>
          {content}
        </Text>
      )}
    </div>
  )
}

function DetailSection({ title, children }: { title: string; children: React.ReactNode }) {
  const { token } = theme.useToken()

  return (
    <div
      style={{
        marginTop: 16,
        padding: 14,
        borderRadius: token.borderRadiusLG,
        border: `1px solid ${token.colorBorder}`,
        background: token.colorFillAlter,
      }}
    >
      <div style={{ marginBottom: 10, fontWeight: 600, color: token.colorText }}>{title}</div>
      {children}
    </div>
  )
}

function LocalProbeSummary({ probe }: { probe: any }) {
  const checkedAt = probe?.checked_at || probe?.auth?.checked_at || probe?.subscription?.checked_at || probe?.codex?.checked_at
  const auth = probe?.auth || {}
  const subscription = probe?.subscription || {}
  const codex = probe?.codex || {}

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        <Tag color={authStateMeta(auth.state).color}>Auth: {authStateMeta(auth.state).label}</Tag>
        <Tag color={planMeta(subscription.plan).color}>Plan: {planMeta(subscription.plan).label}</Tag>
        <Tag color={codexStateMeta(codex.state).color}>Codex: {codexStateMeta(codex.state).label}</Tag>
      </div>
      <SummaryField label="Checked At" value={checkedAt ? formatSyncTime(checkedAt) : ''} />
      <SummaryField label="Auth Details" value={auth.message} code />
      <SummaryField label="Workspace Plan" value={subscription.workspace_plan_type} />
      <SummaryField label="Codex Details" value={codex.message} code />
    </div>
  )
}

function cliproxyStateMeta(sync: any) {
  if (!sync || Object.keys(sync).length === 0) {
    return { color: 'default', label: 'Not Synced' }
  }
  if (sync.remote_state === 'unreachable') {
    return { color: 'error', label: 'Unreachable' }
  }
  if (sync.remote_state === 'not_found') {
    return { color: 'default', label: 'Remote Not Found' }
  }
  if (!sync.uploaded) {
    return { color: 'default', label: 'Not Found' }
  }
  if (sync.remote_state === 'usable') {
    return { color: 'success', label: 'Remote Usable' }
  }
  if (sync.remote_state === 'account_deactivated') {
    return { color: 'error', label: 'Remote Invalid' }
  }
  if (sync.remote_state === 'access_token_invalidated') {
    return { color: 'error', label: 'Remote AT Invalid' }
  }
  if (sync.remote_state === 'unauthorized') {
    return { color: 'error', label: 'Remote Unauthorized' }
  }
  if (sync.remote_state === 'payment_required') {
    return { color: 'warning', label: 'Remote Payment / Permission Required' }
  }
  if (sync.remote_state === 'quota_exhausted') {
    return { color: 'warning', label: 'Remote Quota Exhausted' }
  }
  if (sync.status === 'active') {
    return { color: 'processing', label: 'Remote Active' }
  }
  if (sync.status === 'refreshing') {
    return { color: 'processing', label: 'Remote Refreshing' }
  }
  if (sync.status === 'pending') {
    return { color: 'default', label: 'Remote Pending' }
  }
  if (sync.status === 'error') {
    return { color: 'error', label: 'Remote Error' }
  }
  if (sync.status === 'disabled') {
    return { color: 'default', label: 'Remote Disabled' }
  }
  return { color: 'default', label: 'Not Synced' }
}

function CliproxySyncSummary({ sync }: { sync: any }) {
  const meta = cliproxyStateMeta(sync)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        <Tag color={meta.color}>{meta.label}</Tag>
        {sync?.status ? <Tag>{`status: ${sync.status}`}</Tag> : null}
      </div>
      <SummaryField label="Status Details" value={sync?.status_message} code />
      <SummaryField label="auth-file" value={sync?.name} />
      <SummaryField label="API URL" value={sync?.base_url} />
      <SummaryField label="Synced At" value={sync?.last_synced_at ? formatSyncTime(sync.last_synced_at) : ''} />
      <SummaryField label="Remote Refresh At" value={sync?.last_refresh ? formatSyncTime(sync.last_refresh) : ''} />
      <SummaryField label="Next Retry At" value={sync?.next_retry_after ? formatSyncTime(sync.next_retry_after) : ''} />
      <SummaryField label="Probe Details" value={sync?.last_probe_message} code />
    </div>
  )
}

function ActionMenu({ acc, onRefresh, actions }: { acc: any; onRefresh: () => void; actions: any[] }) {
  const [resultOpen, setResultOpen] = useState(false)
  const [resultTitle, setResultTitle] = useState('')
  const [resultStatus, setResultStatus] = useState<'success' | 'error'>('success')
  const [resultText, setResultText] = useState('')
  const [resultUrl, setResultUrl] = useState('')
  const [resultProbe, setResultProbe] = useState<any>(null)
  const [resultCliproxySync, setResultCliproxySync] = useState<any>(null)

  const showResult = (title: string, status: 'success' | 'error', text: string, url = '', probe: any = null, cliproxySync: any = null) => {
    setResultTitle(title)
    setResultStatus(status)
    setResultText(text)
    setResultUrl(url)
    setResultProbe(probe)
    setResultCliproxySync(cliproxySync)
    setResultOpen(true)
  }

  const copyResultUrl = async () => {
    if (!resultUrl) return
    try {
      await navigator.clipboard.writeText(resultUrl)
      message.success('Link copied')
    } catch {
      message.error('Copy failed')
    }
  }

  const handleAction = async (actionId: string) => {
    const actionLabel = actions.find((item) => item.id === actionId)?.label || actionId

    try {
      const r = await apiFetch(`/actions/${acc.platform}/${acc.id}/${actionId}`, {
        method: 'POST',
        body: JSON.stringify({ params: {} }),
      })
      if (!r.ok) {
        const data = r.data || {}
        const probe = typeof data === 'object' && data ? data.probe || null : null
        const cliproxySync = typeof data === 'object' && data ? data.sync || null : null
        showResult(actionLabel, 'error', r.error || data.message || 'Operation failed', '', probe, cliproxySync)
        onRefresh()
        return
      }
      const data = r.data || {}
      if (data.url || data.checkout_url || data.cashier_url) {
        const targetUrl = data.url || data.checkout_url || data.cashier_url
        message.success('Link generated')
        showResult(actionLabel, 'success', 'Operation completed. Open or copy the link from this dialog.', targetUrl)
      } else {
        message.success(data.message || 'Operation completed')
        const probe = typeof data === 'object' && data ? data.probe || null : null
        const cliproxySync = typeof data === 'object' && data ? data.sync || null : null
        const text =
          probe
            ? String(data.message || 'Operation completed')
            : cliproxySync
            ? String(data.message || 'Operation completed')
            : typeof data === 'string'
            ? data
            : Object.keys(data).length > 0
              ? JSON.stringify(data, null, 2)
              : 'Operation completed'
        showResult(actionLabel, 'success', text, '', probe, cliproxySync)
      }
      onRefresh()
    } catch (e: any) {
      const detail = e?.message ? String(e.message) : 'Request failed'
      message.error(detail)
      showResult(actionLabel, 'error', detail)
    }
  }

  const menuItems: MenuProps['items'] = actions.map((a) => ({
    key: a.id,
    label: a.label,
  }))

  if (actions.length === 0) return null

  return (
    <>
      <Dropdown
        menu={{
          items: menuItems,
          onClick: ({ key }) => handleAction(String(key)),
        }}
      >
        <Button type="link" size="small" icon={<MoreOutlined />} />
      </Dropdown>
      <Modal
        title={resultTitle}
        open={resultOpen}
        onCancel={() => setResultOpen(false)}
        footer={[
          resultUrl ? (
            <Button key="copy" onClick={copyResultUrl}>
              Copy Link
            </Button>
          ) : null,
          resultUrl ? (
            <Button
              key="open"
              type="primary"
              onClick={() => window.open(resultUrl, '_blank', 'noopener,noreferrer')}
            >
              Open Link
            </Button>
          ) : null,
          <Button key="ok" type={resultUrl ? 'default' : 'primary'} onClick={() => setResultOpen(false)}>
            OK
          </Button>,
        ].filter(Boolean)}
        maskClosable={false}
      >
        <Alert
          type={resultStatus}
          showIcon
          message={resultStatus === 'success' ? 'Operation completed' : 'Operation failed'}
          style={{ marginBottom: 12 }}
        />
        {resultProbe ? (
          <div style={{ marginBottom: 12 }}>
            <LocalProbeSummary probe={resultProbe} />
          </div>
        ) : null}
        {resultCliproxySync ? (
          <div style={{ marginBottom: 12 }}>
            <CliproxySyncSummary sync={resultCliproxySync} />
          </div>
        ) : null}
        {resultUrl ? (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Text copyable={{ text: resultUrl }} style={{ wordBreak: 'break-all' }}>
              {resultUrl}
            </Text>
          </Space>
        ) : null}
        {resultText ? (
          <pre
            style={{
              margin: 0,
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              fontFamily: 'monospace',
              fontSize: 12,
            }}
          >
            {resultText}
          </pre>
        ) : null}
      </Modal>
    </>
  )
}

export default function Accounts() {
  const { platform } = useParams<{ platform: string }>()
  const { token } = theme.useToken()
  const [currentPlatform, setCurrentPlatform] = useState(platform || 'trae')
  const [accounts, setAccounts] = useState<any[]>([])
  const [platformActions, setPlatformActions] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [filterStatus, setFilterStatus] = useState('')
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([])

  const [registerModalOpen, setRegisterModalOpen] = useState(false)
  const [addModalOpen, setAddModalOpen] = useState(false)
  const [importModalOpen, setImportModalOpen] = useState(false)
  const [detailModalOpen, setDetailModalOpen] = useState(false)
  const [currentAccount, setCurrentAccount] = useState<any>(null)

  const [registerForm] = Form.useForm()
  const [addForm] = Form.useForm()
  const [detailForm] = Form.useForm()
  const { mode: chatgptRegistrationMode, setMode: setChatgptRegistrationMode } =
    usePersistentChatGPTRegistrationMode()
  const [importText, setImportText] = useState('')
  const [importLoading, setImportLoading] = useState(false)
  const [taskId, setTaskId] = useState<string | null>(null)
  const [registerLoading, setRegisterLoading] = useState(false)
  const [cpaSyncLoading, setCpaSyncLoading] = useState<'pending' | 'selected' | ''>('')
  const [statusSyncLoading, setStatusSyncLoading] = useState<'probe_selected' | 'probe_all' | 'remote_selected' | 'remote_all' | ''>('')

  useEffect(() => {
    if (platform) setCurrentPlatform(platform)
  }, [platform])

  useEffect(() => {
    if (!detailModalOpen || !currentAccount) return
    detailForm.setFieldsValue({
      status: currentAccount.status,
      token: currentAccount.token,
    })
  }, [detailModalOpen, currentAccount, detailForm])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ platform: currentPlatform, page: '1', page_size: '1000' })
      if (search) params.set('email', search)
      if (filterStatus) params.set('status', filterStatus)
      const data = await apiFetch(`/accounts?${params}`)
      setAccounts((data.items || []).map(normalizeAccount))
      setTotal(data.total)
    } finally {
      setLoading(false)
    }
  }, [currentPlatform, search, filterStatus])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    apiFetch(`/actions/${currentPlatform}`)
      .then((data) => setPlatformActions(data.actions || []))
      .catch(() => setPlatformActions([]))
  }, [currentPlatform])

  const copyText = (text: string) => {
    navigator.clipboard.writeText(text)
    message.success('Copied')
  }

  const getRefreshToken = (record: any): string => {
    try {
      const extra = JSON.parse(record.extra_json || '{}')
      return extra.refresh_token || ''
    } catch {
      return ''
    }
  }

  const exportCsv = async () => {
    try {
      const params = new URLSearchParams()
      if (currentPlatform) params.set('platform', currentPlatform)
      if (filterStatus) params.set('status', filterStatus)
      const token = getToken()
      const response = await fetch(`/api/accounts/export?${params.toString()}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      })
      if (response.status === 401) {
        throw new Error('Not authenticated. Please sign in again.')
      }
      if (!response.ok) {
        throw new Error(await response.text())
      }
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${currentPlatform}_accounts.csv`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
      message.success('Export completed')
    } catch (e: any) {
      message.error(`Export failed: ${e.message}`)
    }
  }

  const handleDelete = async (id: number) => {
    await apiFetch(`/accounts/${id}`, { method: 'DELETE' })
    message.success('Deleted')
    load()
  }

  const handleBatchDelete = async () => {
    if (selectedRowKeys.length === 0) return
    await apiFetch('/accounts/batch-delete', {
      method: 'POST',
      body: JSON.stringify({ ids: Array.from(selectedRowKeys) }),
    })
    message.success('Batch delete completed')
    setSelectedRowKeys([])
    load()
  }

  const handleAdd = async () => {
    const values = await addForm.validateFields()
    await apiFetch('/accounts', {
      method: 'POST',
      body: JSON.stringify({ ...values, platform: currentPlatform }),
    })
    message.success('Added successfully')
    setAddModalOpen(false)
    addForm.resetFields()
    load()
  }

  const handleImport = async () => {
    if (!importText.trim()) return
    setImportLoading(true)
    try {
      const lines = importText.trim().split('\n').filter(Boolean)
      const res = await apiFetch('/accounts/import', {
        method: 'POST',
        body: JSON.stringify({ platform: currentPlatform, lines }),
      })
      message.success(`Imported ${res.created} accounts`)
      setImportModalOpen(false)
      setImportText('')
      load()
    } catch (e: any) {
      message.error(`Import failed: ${e.message}`)
    } finally {
      setImportLoading(false)
    }
  }

  const handleRegister = async () => {
    const values = await registerForm.validateFields()
    setRegisterLoading(true)
    try {
      const cfg = await apiFetch('/config')
      const executorType = normalizeExecutorForPlatform(currentPlatform, cfg.default_executor)
      const registerExtra = {
        mail_provider: cfg.mail_provider || 'luckmail',
        laoudo_auth: cfg.laoudo_auth,
        laoudo_email: cfg.laoudo_email,
        laoudo_account_id: cfg.laoudo_account_id,
        gptmail_base_url: cfg.gptmail_base_url,
        gptmail_api_key: cfg.gptmail_api_key,
        gptmail_domain: cfg.gptmail_domain,
        maliapi_base_url: cfg.maliapi_base_url,
        maliapi_api_key: cfg.maliapi_api_key,
        maliapi_domain: cfg.maliapi_domain,
        maliapi_auto_domain_strategy: cfg.maliapi_auto_domain_strategy,
        yescaptcha_key: cfg.yescaptcha_key,
        moemail_api_url: cfg.moemail_api_url,
        moemail_api_key: cfg.moemail_api_key,
        skymail_api_base: cfg.skymail_api_base,
        skymail_token: cfg.skymail_token,
        skymail_domain: cfg.skymail_domain,
        duckmail_address: cfg.duckmail_address,
        duckmail_password: cfg.duckmail_password,
        duckmail_api_url: cfg.duckmail_api_url,
        duckmail_provider_url: cfg.duckmail_provider_url,
        duckmail_bearer: cfg.duckmail_bearer,
        freemail_api_url: cfg.freemail_api_url,
        freemail_admin_token: cfg.freemail_admin_token,
        freemail_username: cfg.freemail_username,
        freemail_password: cfg.freemail_password,
        cfworker_api_url: cfg.cfworker_api_url,
        cfworker_admin_token: cfg.cfworker_admin_token,
        cfworker_custom_auth: cfg.cfworker_custom_auth,
        cfworker_domain: cfg.cfworker_domain,
        cfworker_subdomain: cfg.cfworker_subdomain,
        cfworker_random_subdomain: parseBooleanConfigValue(cfg.cfworker_random_subdomain),
        cfworker_fingerprint: cfg.cfworker_fingerprint,
        smstome_cookie: cfg.smstome_cookie,
        smstome_country_slugs: cfg.smstome_country_slugs,
        smstome_phone_attempts: cfg.smstome_phone_attempts,
        smstome_otp_timeout_seconds: cfg.smstome_otp_timeout_seconds,
        smstome_poll_interval_seconds: cfg.smstome_poll_interval_seconds,
        smstome_sync_max_pages_per_country: cfg.smstome_sync_max_pages_per_country,
        luckmail_base_url: cfg.luckmail_base_url,
        luckmail_api_key: cfg.luckmail_api_key,
        luckmail_email_type: cfg.luckmail_email_type,
        luckmail_domain: cfg.luckmail_domain,
      }
      const chatgptRegistrationRequestAdapter =
        buildChatGPTRegistrationRequestAdapter(
          currentPlatform,
          chatgptRegistrationMode,
        )
      const adaptedRegisterExtra = chatgptRegistrationRequestAdapter
        ? chatgptRegistrationRequestAdapter.extendExtra(registerExtra)
        : registerExtra

      const res = await apiFetch('/tasks/register', {
        method: 'POST',
        body: JSON.stringify({
          platform: currentPlatform,
          count: values.count,
          concurrency: values.concurrency,
          register_delay_seconds: values.register_delay_seconds || 0,
          executor_type: executorType,
          captcha_solver: cfg.default_captcha_solver || 'yescaptcha',
          proxy: null,
          extra: adaptedRegisterExtra,
        }),
      })
      setTaskId(res.task_id)
    } finally {
      setRegisterLoading(false)
    }
  }

  const handleDetailSave = async () => {
    const values = await detailForm.validateFields()
    await apiFetch(`/accounts/${currentAccount.id}`, {
      method: 'PATCH',
      body: JSON.stringify(values),
    })
    message.success('Saved')
    setDetailModalOpen(false)
    load()
  }

  const showCpaSyncResult = (title: string, result: any) => {
    const lines = (result.items || [])
      .flatMap((item: any) =>
        (item.results || []).map((syncResult: any) => ({
          email: item.email,
          platform: item.platform,
          ok: Boolean(syncResult.ok),
          name: syncResult.name || 'CPA',
          msg: syncResult.msg || '',
        })),
      )
      .filter((item: any) => !item.ok)
      .map((item: any) => `[${item.platform}] ${item.email || '-'} / ${item.name}: ${item.msg || 'Failed'}`)

    if (lines.length === 0) return

    Modal.info({
      title,
      width: 760,
      content: (
        <pre
          style={{
            margin: 0,
            maxHeight: 360,
            overflow: 'auto',
            padding: 12,
            borderRadius: 8,
            background: 'rgba(127,127,127,0.08)',
            fontSize: 12,
            lineHeight: 1.5,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {lines.join('\n')}
        </pre>
      ),
    })
  }

  const showBatchActionResult = (title: string, result: any) => {
    const lines = (result.items || [])
      .filter((item: any) => !item.ok)
      .map((item: any) => `[${item.id || '-'}] ${item.email || '-'}: ${item.message || 'Failed'}`)

    if (lines.length === 0) return

    Modal.info({
      title,
      width: 760,
      content: (
        <pre
          style={{
            margin: 0,
            maxHeight: 360,
            overflow: 'auto',
            padding: 12,
            borderRadius: 8,
            background: 'rgba(127,127,127,0.08)',
            fontSize: 12,
            lineHeight: 1.5,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {lines.join('\n')}
        </pre>
      ),
    })
  }

  const handleCpaBackfill = async (mode: 'pending' | 'selected') => {
    if (currentPlatform !== 'chatgpt') return

    const body: Record<string, unknown> = {
      platforms: ['chatgpt'],
    }

    if (mode === 'selected') {
      const accountIds = Array.from(selectedRowKeys)
        .map((value) => Number(value))
        .filter((value) => Number.isInteger(value) && value > 0)

      if (accountIds.length === 0) {
        message.warning('Select accounts to upload first')
        return
      }
      body.account_ids = accountIds
    } else {
      body.pending_only = true
      if (filterStatus) body.status = filterStatus
      if (search) body.email = search
    }

    setCpaSyncLoading(mode)
    try {
      const result = await apiFetch('/integrations/backfill', {
        method: 'POST',
        body: JSON.stringify(body),
      })

      const actionLabel = mode === 'selected' ? 'Remote Backfill for Selected Accounts' : 'Remote Backfill for Missing Accounts'
      if (!result.total) {
        message.info('No accounts to process')
      } else if (!result.failed && !result.skipped) {
        message.success(`${actionLabel} completed: ${result.success} succeeded / ${result.total}`)
      } else if (!result.failed) {
        message.success(`${actionLabel} completed: ${result.success} succeeded, ${result.skipped} skipped / ${result.total}`)
      } else if (!result.success) {
        message.error(`${actionLabel} failed: ${result.success} succeeded, ${result.skipped} skipped / ${result.total}`)
      } else {
        message.warning(`${actionLabel} partially completed: ${result.success} succeeded, ${result.skipped} skipped / ${result.total}`)
      }

      showCpaSyncResult(`${actionLabel} Result`, result)
      await load()
    } catch (e: any) {
      message.error(`CPA upload failed: ${e.message}`)
    } finally {
      setCpaSyncLoading('')
    }
  }

  const handleBatchStatusSync = async (kind: 'probe' | 'remote', scope: 'selected' | 'all') => {
    if (currentPlatform !== 'chatgpt') return

    const loadingKey = `${kind}_${scope}` as typeof statusSyncLoading
    const actionId = kind === 'probe' ? 'probe_local_status' : 'sync_cliproxyapi_status'
    const actionLabel = kind === 'probe' ? 'Local Status Sync' : 'CLIProxyAPI Status Sync'
    const scopeLabel = scope === 'selected' ? 'Selected Accounts ' : 'Filtered Accounts '
    const toastKey = `status-sync:${loadingKey}`

    const body: Record<string, unknown> = {
      params: {},
    }

    if (scope === 'selected') {
      const accountIds = Array.from(selectedRowKeys)
        .map((value) => Number(value))
        .filter((value) => Number.isInteger(value) && value > 0)

      if (accountIds.length === 0) {
        message.warning('Select accounts to sync first')
        return
      }
      body.account_ids = accountIds
    } else {
      body.all_filtered = true
      if (search) body.email = search
      if (filterStatus) body.status = filterStatus
    }

    setStatusSyncLoading(loadingKey)
    message.loading({ content: `${scopeLabel}${actionLabel} in progress...`, key: toastKey, duration: 0 })
    try {
      const result = await apiFetch(`/actions/${currentPlatform}/${actionId}/batch`, {
        method: 'POST',
        body: JSON.stringify(body),
      })

      if (!result.total) {
        message.info({ content: 'No accounts to process', key: toastKey })
      } else if (!result.failed) {
        message.success({ content: `${scopeLabel}${actionLabel} completed: ${result.success} succeeded / ${result.total}`, key: toastKey })
      } else if (!result.success) {
        message.error({ content: `${scopeLabel}${actionLabel} failed: ${result.success} succeeded / ${result.total}`, key: toastKey })
      } else {
        message.warning({ content: `${scopeLabel}${actionLabel} partially completed: ${result.success} succeeded / ${result.total}`, key: toastKey })
      }

      showBatchActionResult(`${scopeLabel}${actionLabel} Result`, result)
      await load()
    } catch (e: any) {
      message.error({ content: `${actionLabel} failed: ${e.message}`, key: toastKey })
    } finally {
      setStatusSyncLoading('')
    }
  }

  const getStatusSyncScope = (): 'selected' | 'all' => (selectedRowKeys.length > 0 ? 'selected' : 'all')

  const getBackfillScope = (): 'selected' | 'pending' => (selectedRowKeys.length > 0 ? 'selected' : 'pending')

  const backfillButtonLabel = () => {
    const scope = getBackfillScope()
    const count = scope === 'selected' ? selectedRowKeys.length : total
    return scope === 'selected' ? `Backfill Selected Missing Remote Entries (${count})` : `Backfill Missing Remote Entries (${count})`
  }

  const isChatgptPlatform = currentPlatform === 'chatgpt'
  const monospaceStyle: React.CSSProperties = {
    fontFamily: 'SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace',
    fontSize: 12,
  }
  const secondaryTextStyle: React.CSSProperties = {
    fontSize: 12,
    color: token.colorTextSecondary,
  }
  const cellStackStyle: React.CSSProperties = {
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
    minWidth: 0,
  }
  const secretPreviewStyle: React.CSSProperties = {
    ...monospaceStyle,
    filter: 'blur(4px)',
    whiteSpace: 'nowrap',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    maxWidth: '100%',
    opacity: 0.9,
  }
  const compactPanelStyle: React.CSSProperties = {
    padding: '8px 10px',
    borderRadius: token.borderRadiusLG,
    border: `1px solid ${token.colorBorder}`,
    background: token.colorFillAlter,
  }

  const columns: any[] = [
    {
      title: 'Email',
      dataIndex: 'email',
      key: 'email',
      width: 260,
      render: (text: string, record: any) => (
        <div style={cellStackStyle}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
            <Text
              style={{ ...monospaceStyle, flex: 1, minWidth: 0, whiteSpace: 'nowrap' }}
              ellipsis={{ tooltip: text }}
            >
              {text}
            </Text>
            <Button type="text" size="small" icon={<CopyOutlined />} onClick={() => copyText(text)} />
          </div>
          <Text type="secondary" style={secondaryTextStyle} ellipsis={{ tooltip: record.user_id || `Account #${record.id}` }}>
            {record.user_id ? `UID: ${record.user_id}` : `Account #${record.id}`}
          </Text>
        </div>
      ),
    },
    {
      title: 'Password',
      dataIndex: 'password',
      key: 'password',
      width: 150,
      render: (text: string) => (
        <Space size={6} style={{ width: '100%', justifyContent: 'space-between' }}>
          <Text style={{ ...secretPreviewStyle, maxWidth: 90 }} title={text}>
            {text}
          </Text>
          <Button type="text" size="small" icon={<CopyOutlined />} onClick={() => copyText(text)} />
        </Space>
      ),
    },
    {
      title: 'RT',
      key: 'refresh_token',
      width: 120,
      render: (_: any, record: any) => {
        const rt = getRefreshToken(record)
        if (!rt) return <span style={{ color: '#ccc' }}>-</span>
        return (
          <Space size={6} style={{ width: '100%', justifyContent: 'space-between' }}>
            <Text style={{ ...secretPreviewStyle, fontSize: 11, maxWidth: 58 }} title={rt}>
              {rt}
            </Text>
            <Button type="text" size="small" icon={<CopyOutlined />} onClick={() => copyText(rt)} />
          </Space>
        )
      },
    },
    {
      title: 'Status',
      dataIndex: 'status',
      key: 'status',
      width: 130,
      render: (status: string, record: any) => {
        if (status === 'failed' || status === 'invalid') {
          return <Tag color={STATUS_COLORS[status] || 'error'}>{status}</Tag>
        }
        if (record.referred_count >= 20) {
          return <Tag color="green">✓ Max 20 Ref</Tag>
        }
        return <Tag color={STATUS_COLORS[status] || 'default'}>{status}</Tag>
      },
    },
  ]

  if (isChatgptPlatform) {
    columns.push(
      {
        title: 'Local Status',
        key: 'chatgpt_local_state',
        width: 220,
        render: (_: any, record: any) => {
          const auth = record.chatgptLocal?.auth || {}
          const subscription = record.chatgptLocal?.subscription || {}
          const codex = record.chatgptLocal?.codex || {}
          const authMeta = authStateMeta(auth.state)
          const planTag = planMeta(subscription.plan)
          const codexMeta = codexStateMeta(codex.state)

          return (
            <div style={{ ...cellStackStyle, ...compactPanelStyle }}>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                <Tag color={authMeta.color}>{authMeta.label}</Tag>
                <Tag color={planTag.color}>{planTag.label}</Tag>
                <Tag color={codexMeta.color}>Codex {codexMeta.label}</Tag>
              </div>
            </div>
          )
        },
      },
      {
        title: 'CLIProxyAPI',
        key: 'cliproxy_sync',
        width: 170,
        render: (_: any, record: any) => {
          const sync = record.cliproxySync || {}
          const meta = cliproxyStateMeta(sync)

          return (
            <div style={{ ...cellStackStyle, ...compactPanelStyle }}>
              <Tag color={meta.color}>{meta.label}</Tag>
            </div>
          )
        },
      },
    )
  } else {
    columns.push(
      {
        title: 'Region',
        dataIndex: 'region',
        key: 'region',
        width: 100,
        render: (text: string) => text || '-',
      },
      {
        title: 'Trial Link',
        dataIndex: 'cashier_url',
        key: 'cashier_url',
        width: 120,
        render: (url: string) =>
          url ? (
            <Space size={0}>
              <Button type="text" size="small" icon={<CopyOutlined />} onClick={() => copyText(url)} />
              <Button type="text" size="small" icon={<LinkOutlined />} onClick={() => window.open(url, '_blank')} />
            </Space>
          ) : (
            '-'
          ),
      },
      {
        title: 'Ref Link',
        dataIndex: 'ref_link',
        key: 'ref_link',
        width: 120,
        render: (url: string) =>
          url ? (
            <Space size={0}>
              <Button type="text" size="small" icon={<CopyOutlined />} onClick={() => copyText(url)} />
              <Button type="text" size="small" icon={<LinkOutlined />} onClick={() => window.open(url, '_blank')} />
            </Space>
          ) : (
            '-'
          ),
      },
      {
        title: 'Parent Email',
        dataIndex: 'parent_email',
        key: 'parent_email',
        width: 180,
        render: (text: string) =>
          text && text.toUpperCase() !== 'MASTER' ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 4, minWidth: 0 }}>
              <Text style={{ ...monospaceStyle, flex: 1, minWidth: 0 }} ellipsis={{ tooltip: text }}>
                {text}
              </Text>
              <Button type="text" size="small" icon={<CopyOutlined />} onClick={() => copyText(text)} />
            </div>
          ) : (
            <Text type="secondary">{text || '-'}</Text>
          ),
      },
      {
        title: 'Refs',
        dataIndex: 'referred_count',
        key: 'referred_count',
        width: 80,
        render: (count: number) => {
          const n = count || 0
          const color = n >= 20 ? 'green' : n >= 10 ? 'orange' : 'default'
          return <Tag color={color}>{n}/20</Tag>
        },
      },
    )
  }

  columns.push(
    {
      title: 'Created At',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 132,
      render: (text: string) => {
        const formatted = formatCreatedAt(text)
        return (
          <div style={cellStackStyle}>
            <Text style={{ fontSize: 13 }}>{formatted.date}</Text>
            {formatted.time ? <Text type="secondary" style={secondaryTextStyle}>{formatted.time}</Text> : null}
          </div>
        )
      },
    },
    {
      title: 'Actions',
      key: 'action',
      width: 150,
      fixed: isChatgptPlatform ? 'right' : undefined,
      render: (_: any, record: any) => (
        <Space size={4} wrap>
          <Button type="link" size="small" onClick={() => { setCurrentAccount(record); setDetailModalOpen(true); }}>
            Details
          </Button>
          <Popconfirm title="Delete this account?" onConfirm={() => handleDelete(record.id)}>
            <Button type="link" size="small" danger>
              Delete
            </Button>
          </Popconfirm>
          <ActionMenu acc={record} onRefresh={load} actions={platformActions} />
        </Space>
      ),
    },
  )

  const statusSyncMenuItems: MenuProps['items'] = [
    {
      key: `probe:${getStatusSyncScope()}`,
      label:
        getStatusSyncScope() === 'selected'
          ? `Sync Local Status for Selected (${selectedRowKeys.length})`
          : `Sync Local Status for Filtered (${total})`,
      disabled: getStatusSyncScope() === 'selected' ? selectedRowKeys.length === 0 : total === 0,
    },
    {
      key: `remote:${getStatusSyncScope()}`,
      label:
        getStatusSyncScope() === 'selected'
          ? `Sync CLIProxyAPI Status for Selected (${selectedRowKeys.length})`
          : `Sync CLIProxyAPI Status for Filtered (${total})`,
      disabled: getStatusSyncScope() === 'selected' ? selectedRowKeys.length === 0 : total === 0,
    },
  ]

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
        <Space>
          <Input.Search
            placeholder="Search email..."
            allowClear
            value={searchInput}
            onChange={(e) => {
              const value = e.target.value
              setSearchInput(value)
              if (!value) {
                setSearch('')
              }
            }}
            onSearch={(value) => {
              const normalized = value.trim()
              setSearchInput(value)
              setSearch(normalized)
            }}
            style={{ width: 200 }}
          />
          <Select
            placeholder="Filter by status"
            allowClear
            style={{ width: 120 }}
            onChange={setFilterStatus}
            options={[
              { value: 'registered', label: 'Registered' },
              { value: 'trial', label: 'Trial' },
              { value: 'subscribed', label: 'Subscribed' },
              { value: 'expired', label: 'Expired' },
              { value: 'invalid', label: 'Invalid' },
            ]}
          />
          <Text type="secondary">{total} accounts</Text>
          {selectedRowKeys.length > 0 && (
            <Text type="success">{selectedRowKeys.length} selected</Text>
          )}
        </Space>
        <Space>
          {currentPlatform === 'chatgpt' && (
            <Dropdown
              trigger={['click']}
              menu={{
                items: statusSyncMenuItems,
                onClick: ({ key }) => {
                  const [kind, scope] = String(key).split(':') as ['probe' | 'remote', 'selected' | 'all']
                  handleBatchStatusSync(kind, scope)
                },
              }}
            >
              <Button
                icon={<SyncOutlined />}
                loading={statusSyncLoading !== ''}
                disabled={total === 0}
              >
                Status Sync
              </Button>
            </Dropdown>
          )}
          {currentPlatform === 'chatgpt' && (
            <Popconfirm
              title={
                getBackfillScope() === 'selected'
                  ? `Backfill missing remote auth-files for the ${selectedRowKeys.length} selected accounts?`
                  : 'Backfill accounts in the current filter whose remote auth-file is missing but local status is valid?'
              }
              onConfirm={() => handleCpaBackfill(getBackfillScope())}
            >
              <Button
                loading={cpaSyncLoading === 'pending' || cpaSyncLoading === 'selected'}
                icon={<UploadOutlined />}
                disabled={getBackfillScope() === 'selected' ? selectedRowKeys.length === 0 : total === 0}
              >
                {backfillButtonLabel()}
              </Button>
            </Popconfirm>
          )}
          {selectedRowKeys.length > 0 && (
            <Popconfirm title={`Delete ${selectedRowKeys.length} selected accounts?`} onConfirm={handleBatchDelete}>
              <Button danger icon={<DeleteOutlined />}>Delete {selectedRowKeys.length}</Button>
            </Popconfirm>
          )}
          <Button icon={<UploadOutlined />} onClick={() => setImportModalOpen(true)}>Import</Button>
          <Button icon={<DownloadOutlined />} onClick={exportCsv} disabled={accounts.length === 0}>Export</Button>
          <Button icon={<PlusOutlined />} onClick={() => setAddModalOpen(true)}>Add</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setRegisterModalOpen(true)}>Register</Button>
          <Button icon={<ReloadOutlined spin={loading} />} onClick={load} />
        </Space>
      </div>

      <Table
        rowKey="id"
        columns={columns}
        dataSource={accounts}
        loading={loading}
        size="middle"
        rowSelection={{
          selectedRowKeys,
          onChange: setSelectedRowKeys,
        }}
        pagination={{ pageSize: 20, showSizeChanger: false }}
        scroll={{ x: isChatgptPlatform ? 1440 : 1360 }}
        onRow={(record) => ({
          onDoubleClick: () => {
            setCurrentAccount(record)
            setDetailModalOpen(true)
          },
        })}
      />

      <Modal
        title={`Register ${currentPlatform}`}
        open={registerModalOpen}
        onCancel={() => { setRegisterModalOpen(false); setTaskId(null); registerForm.resetFields(); }}
        footer={null}
        width={500}
        maskClosable={false}
      >
        {!taskId ? (
          <Form form={registerForm} layout="vertical" onFinish={handleRegister}>
            <Form.Item name="count" label="Registration Count" initialValue={1} rules={[{ required: true }]}>
              <Input type="number" min={1} />
            </Form.Item>
            <Form.Item name="concurrency" label="Concurrency" initialValue={1} rules={[{ required: true }]}>
              <Input type="number" min={1} max={5} />
            </Form.Item>
            <Form.Item name="register_delay_seconds" label="Delay per Registration (seconds)" initialValue={0}>
              <InputNumber min={0} precision={1} step={0.5} style={{ width: '100%' }} placeholder="0 = no delay" />
            </Form.Item>
            {currentPlatform === 'chatgpt' && (
              <Form.Item label="ChatGPT Token Mode">
                <ChatGPTRegistrationModeSwitch
                  mode={chatgptRegistrationMode}
                  onChange={setChatgptRegistrationMode}
                />
              </Form.Item>
            )}
            <Form.Item>
              <Button type="primary" htmlType="submit" block loading={registerLoading}>
                Start Registration
              </Button>
            </Form.Item>
          </Form>
        ) : (
          <TaskLogPanel taskId={taskId} onDone={() => { load(); }} />
        )}
      </Modal>

      <Modal
        title="Add Account Manually"
        open={addModalOpen}
        onCancel={() => { setAddModalOpen(false); addForm.resetFields(); }}
        onOk={handleAdd}
        maskClosable={false}
      >
        <Form form={addForm} layout="vertical">
          <Form.Item name="email" label="Email" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="password" label="Password" rules={[{ required: true }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item name="token" label="Token">
            <Input />
          </Form.Item>
          <Form.Item name="cashier_url" label="Trial Link">
            <Input />
          </Form.Item>
          <Form.Item name="status" label="Status" initialValue="registered">
            <Select
              options={[
                { value: 'registered', label: 'Registered' },
                { value: 'trial', label: 'Trial' },
                { value: 'subscribed', label: 'Subscribed' },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="Bulk Import"
        open={importModalOpen}
        onCancel={() => { setImportModalOpen(false); setImportText(''); }}
        onOk={handleImport}
        confirmLoading={importLoading}
        maskClosable={false}
      >
        <p style={{ marginBottom: 8, fontSize: 12, color: '#7a8ba3' }}>
          Format per line: <code style={{ background: 'rgba(255,255,255,0.1)', padding: '2px 4px', borderRadius: 4 }}>email password [cashier_url]</code>
        </p>
        <Input.TextArea
          value={importText}
          onChange={(e) => setImportText(e.target.value)}
          rows={8}
          style={{ fontFamily: 'monospace' }}
        />
      </Modal>

      <Modal
        title="Account Details"
        open={detailModalOpen}
        onCancel={() => setDetailModalOpen(false)}
        onOk={handleDetailSave}
        maskClosable={false}
        width={760}
        styles={{ body: { maxHeight: '72vh', overflowY: 'auto' } }}
      >
        {currentAccount && (
          <>
            <Form form={detailForm} layout="vertical" initialValues={currentAccount}>
              <Form.Item name="status" label="Status">
                <Select
                  options={[
                    { value: 'registered', label: 'Registered' },
                    { value: 'trial', label: 'Trial' },
                    { value: 'subscribed', label: 'Subscribed' },
                    { value: 'expired', label: 'Expired' },
                    { value: 'invalid', label: 'Invalid' },
                  ]}
                />
              </Form.Item>
              <Form.Item name="token" label="Access Token">
                <Input.TextArea rows={2} style={{ fontFamily: 'monospace' }} />
              </Form.Item>
            </Form>
            {(() => {
              const rt = getRefreshToken(currentAccount)
              if (!rt) return null
              return (
                <div style={{ marginTop: 8 }}>
                  <div style={{ marginBottom: 4, fontWeight: 500, fontSize: 13 }}>Refresh Token</div>
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'flex-start',
                      gap: 8,
                      background: token.colorFillAlter,
                      border: `1px solid ${token.colorBorder}`,
                      borderRadius: token.borderRadius,
                      padding: '8px 10px',
                    }}
                  >
                    <Text
                      style={{ fontFamily: 'monospace', fontSize: 11, wordBreak: 'break-all', flex: 1, userSelect: 'text' }}
                      copyable={{ text: rt, tooltips: ['Copy RT', 'Copied'] }}
                    >
                      {rt}
                    </Text>
                  </div>
                </div>
              )
            })()}
            {currentPlatform === 'chatgpt' ? (
              <DetailSection title="Local Runtime Status">
                {currentAccount.chatgptLocal && Object.keys(currentAccount.chatgptLocal).length > 0 ? (
                  <LocalProbeSummary probe={currentAccount.chatgptLocal} />
                ) : (
                  <Text type="secondary">No local probe data yet. Use the action menu to run a local status probe.</Text>
                )}
              </DetailSection>
            ) : null}
            {currentPlatform === 'chatgpt' ? (
              <DetailSection title="CLIProxyAPI Status">
                {currentAccount.cliproxySync && Object.keys(currentAccount.cliproxySync).length > 0 ? (
                  <CliproxySyncSummary sync={currentAccount.cliproxySync} />
                ) : (
                  <Text type="secondary">Not synced yet. Use the action menu to sync CLIProxyAPI status.</Text>
                )}
              </DetailSection>
            ) : null}
          </>
        )}
      </Modal>
    </div>
  )
}
