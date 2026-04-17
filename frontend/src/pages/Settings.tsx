import { useEffect, useState } from 'react'
import { App, Card, Form, Input, Select, Button, message, Tabs, Space, Tag, Typography, Modal, QRCode, Switch } from 'antd'
import {
  SaveOutlined,
  EyeOutlined,
  EyeInvisibleOutlined,
  MailOutlined,
  SafetyOutlined,
  ApiOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  SyncOutlined,
  PlusOutlined,
  LockOutlined,
} from '@ant-design/icons'
import { parseBooleanConfigValue } from '@/lib/configValueParsers'
import { apiFetch } from '@/lib/utils'

const SELECT_FIELDS: Record<string, { label: string; value: string }[]> = {
  mail_provider: [
    { label: 'LuckMail (order OTP / purchased mailbox)', value: 'luckmail' },
    { label: 'Laoudo (fixed mailbox)', value: 'laoudo' },
    { label: 'TempMail.lol (auto-generated)', value: 'tempmail_lol' },
    { label: 'TempMailo (UI 2-Tab Scraping)', value: 'tempmail' },
    { label: 'SkyMail (CloudMail API)', value: 'skymail' },
    { label: 'DuckMail (auto-generated)', value: 'duckmail' },
    { label: 'MoeMail (sall.cc)', value: 'moemail' },
    { label: 'YYDS Mail / MaliAPI', value: 'maliapi' },
    { label: 'GPTMail', value: 'gptmail' },
    { label: 'OpenTrashMail', value: 'opentrashmail' },
    { label: 'Freemail (self-hosted CF Worker)', value: 'freemail' },
    { label: 'CF Worker (custom domain)', value: 'cfworker' },
  ],
  maliapi_auto_domain_strategy: [
    { label: 'balanced', value: 'balanced' },
    { label: 'prefer_owned', value: 'prefer_owned' },
    { label: 'prefer_public', value: 'prefer_public' },
  ],
  default_executor: [
    { label: 'API Protocol (no browser)', value: 'protocol' },
    { label: 'Headless Browser', value: 'headless' },
    { label: 'Headed Browser', value: 'headed' },
  ],
  default_captcha_solver: [
    { label: 'YesCaptcha', value: 'yescaptcha' },
    { label: 'Local Solver (Camoufox)', value: 'local_solver' },
    { label: 'Manual', value: 'manual' },
  ],
  cpa_cleanup_enabled: [
    { label: 'Off', value: '0' },
    { label: 'On', value: '1' },
  ],
  codex_proxy_upload_type: [
    { label: 'AT (Access Token, Recommended)', value: 'at' },
    { label: 'RT (Refresh Token)', value: 'rt' },
  ],
}

const TAB_ITEMS = [
  {
    key: 'register',
    label: 'Registration',
    icon: <ApiOutlined />,
    sections: [
      {
        title: 'Default Registration Mode',
        desc: 'Controls how registration tasks are executed',
        fields: [{ key: 'default_executor', label: 'Executor Type', type: 'select' }],
      },
    ],
  },
  {
    key: 'mailbox',
    label: 'Mailbox',
    icon: <MailOutlined />,
    sections: [
      {
        title: 'Default Mailbox Provider',
        desc: 'Choose which mailbox service to use for registrations',
        fields: [{ key: 'mail_provider', label: 'Mailbox Provider', type: 'select' }],
      },
      {
        title: 'Laoudo',
        provider: 'laoudo',
        desc: 'Fixed mailbox, configured manually',
        fields: [
          { key: 'laoudo_email', label: 'Email Address', placeholder: 'xxx@laoudo.com' },
          { key: 'laoudo_account_id', label: 'Account ID', placeholder: '563' },
          { key: 'laoudo_auth', label: 'JWT Token', placeholder: 'eyJ...', secret: true },
        ],
      },
      {
        title: 'Freemail',
        provider: 'freemail',
        desc: 'Self-hosted mailbox based on Cloudflare Worker, supporting admin token or username/password auth',
        fields: [
          { key: 'freemail_api_url', label: 'API URL', placeholder: 'https://mail.example.com' },
          { key: 'freemail_admin_token', label: 'Admin Token', secret: true },
          { key: 'freemail_username', label: 'Username (Optional)' },
          { key: 'freemail_password', label: 'Password (Optional)', secret: true },
        ],
      },
      {
        title: 'MoeMail',
        provider: 'moemail',
        desc: 'Automatically creates an account and generates a temporary mailbox',
        fields: [
          { key: 'moemail_api_url', label: 'API URL', placeholder: 'https://sall.cc' },
          { key: 'moemail_api_key', label: 'API Key', secret: true },
        ],
      },
      {
        title: 'SkyMail',
        provider: 'skymail',
        desc: 'CloudMail-compatible API (addUser / emailList)',
        fields: [
          { key: 'skymail_api_base', label: 'API Base', placeholder: 'https://api.skymail.ink' },
          { key: 'skymail_token', label: 'Authorization Token', secret: true },
          { key: 'skymail_domain', label: 'Mailbox Domain', placeholder: 'mail.example.com' },
        ],
      },
      {
        title: 'YYDS Mail / MaliAPI',
        provider: 'maliapi',
        desc: 'Creates temporary mailboxes via API key and polls inbox messages',
        fields: [
          { key: 'maliapi_base_url', label: 'API URL', placeholder: 'https://maliapi.215.im/v1' },
          { key: 'maliapi_api_key', label: 'API Key', secret: true },
          { key: 'maliapi_domain', label: 'Mailbox Domain (Optional)', placeholder: 'example.com' },
          { key: 'maliapi_auto_domain_strategy', label: 'Auto Domain Strategy', type: 'select' },
        ],
      },
      {
        title: 'GPTMail',
        provider: 'gptmail',
        desc: 'Generates temporary mailboxes via GPTMail and polls messages. If you know a valid domain, it can also build a local random address.',
        fields: [
          { key: 'gptmail_base_url', label: 'API URL', placeholder: 'https://mail.chatgpt.org.uk' },
          { key: 'gptmail_api_key', label: 'API Key', secret: true, placeholder: 'gpt-test' },
          { key: 'gptmail_domain', label: 'Mailbox Domain (Optional)', placeholder: 'example.com' },
        ],
      },
      {
        title: 'OpenTrashMail',
        provider: 'opentrashmail',
        desc: 'Connects to OpenTrashMail. Supports polling /json/<email> and locally building random addresses when the domain is known.',
        fields: [
          { key: 'opentrashmail_api_url', label: 'API URL', placeholder: 'http://mail.example.com:8085' },
          { key: 'opentrashmail_domain', label: 'Mailbox Domain (Optional)', placeholder: 'xiyoufm.com' },
          { key: 'opentrashmail_password', label: 'Site Password (Optional)', secret: true, placeholder: 'Only when PASSWORD is enabled' },
        ],
      },
      {
        title: 'TempMail.lol',
        provider: 'tempmail_lol',
        desc: 'Auto-generated mailbox with no setup required. Needs proxy access in blocked regions.',
        fields: [],
      },
      {
        title: 'TempMailo',
        provider: 'tempmail',
        desc: 'Uses temp-mailo.org UI scraping with a 2-tab browser flow as a fallback mailbox strategy.',
        fields: [],
      },
      {
        title: 'DuckMail',
        provider: 'duckmail',
        desc: 'Auto-generated mailbox with random account creation',
        fields: [
          { key: 'duckmail_api_url', label: 'Web URL', placeholder: 'https://www.duckmail.sbs' },
          { key: 'duckmail_provider_url', label: 'Provider URL', placeholder: 'https://api.duckmail.sbs' },
          { key: 'duckmail_bearer', label: 'Bearer Token', placeholder: 'kevin273945', secret: true },
          { key: 'duckmail_domain', label: 'Custom Domain', placeholder: 'Leave empty to infer from Provider URL' },
          { key: 'duckmail_api_key', label: 'API Key (private domain)', placeholder: 'dk_xxx (from domain.duckmail.sbs)', secret: true },
        ],
      },
      {
        title: 'CF Worker Mailbox',
        provider: 'cfworker',
        desc: 'Self-hosted temporary mailbox service built on Cloudflare Worker',
        fields: [
          { key: 'cfworker_api_url', label: 'API URL', placeholder: 'https://apimail.example.com' },
          { key: 'cfworker_admin_token', label: 'Admin Token', secret: true },
          { key: 'cfworker_custom_auth', label: 'Site Password', secret: true },
          { key: 'cfworker_subdomain', label: 'Fixed Subdomain', placeholder: 'mail / pool-a' },
          { key: 'cfworker_random_subdomain', label: 'Random Subdomain', type: 'boolean' },
          { key: 'cfworker_fingerprint', label: 'Fingerprint', placeholder: '6703363b...' },
        ],
      },
      {
        title: 'LuckMail',
        provider: 'luckmail',
        desc: 'ChatGPT uses purchased mailboxes; other platforms keep the legacy order/OTP flow',
        fields: [
          { key: 'luckmail_base_url', label: 'Platform URL', placeholder: 'https://mails.luckyous.com' },
          { key: 'luckmail_api_key', label: 'API Key', secret: true },
          { key: 'luckmail_email_type', label: 'Mailbox Type (Optional)', placeholder: 'ms_graph / ms_imap / self_built' },
          { key: 'luckmail_domain', label: 'Mailbox Domain (Optional)', placeholder: 'outlook.com / gmail.com' },
        ],
      },
    ],
  },
  {
    key: 'captcha',
    label: 'Captcha',
    icon: <SafetyOutlined />,
    sections: [
      {
        title: 'Captcha Service',
        desc: 'Used to solve anti-bot verification during registration',
        fields: [
          { key: 'default_captcha_solver', label: 'Default Service', type: 'select' },
          { key: 'yescaptcha_key', label: 'YesCaptcha Key', secret: true },
        ],
      },
    ],
  },
  {
    key: 'chatgpt',
    label: 'ChatGPT',
    icon: <ApiOutlined />,
    sections: [
      {
        title: 'CPA Panel',
        desc: 'Automatically upload successful registrations to the CPA platform',
        fields: [
          { key: 'cpa_api_url', label: 'API URL', placeholder: 'https://your-cpa.example.com' },
          { key: 'cpa_api_key', label: 'API Key', secret: true },
        ],
      },
      {
        title: 'Sub2API Panel',
        desc: 'Automatically upload successful registrations to the Sub2API backend',
        fields: [
          { key: 'sub2api_api_url', label: 'API URL', placeholder: 'https://your-sub2api.example.com' },
          { key: 'sub2api_api_key', label: 'API Key', secret: true },
          { key: 'sub2api_group_ids', label: 'Group IDs', placeholder: 'Comma-separated, for example 2,4,8' },
        ],
      },
      {
        title: 'CPA Auto Maintenance',
        desc: 'Periodically deletes status=error credentials and auto-registers ChatGPT when the remaining count drops below the threshold',
        fields: [
          { key: 'cpa_cleanup_enabled', label: 'Auto Maintenance', type: 'select' },
          { key: 'cpa_cleanup_interval_minutes', label: 'Check Interval (minutes)', placeholder: '60' },
          { key: 'cpa_cleanup_threshold', label: 'Minimum Credential Threshold', placeholder: '5' },
          { key: 'cpa_cleanup_concurrency', label: 'Backfill Concurrency', placeholder: '1' },
          { key: 'cpa_cleanup_register_delay_seconds', label: 'Delay per Registration (seconds)', placeholder: '0' },
        ],
      },
      {
        title: 'Team Manager',
        desc: 'Upload to a self-hosted Team Manager system',
        fields: [
          { key: 'team_manager_url', label: 'API URL', placeholder: 'https://your-tm.example.com' },
          { key: 'team_manager_key', label: 'API Key', secret: true },
        ],
      },
      {
        title: 'CodexProxy',
        desc: 'Automatically upload successful registrations to CodexProxy',
        fields: [
          { key: 'codex_proxy_url', label: 'API URL', placeholder: 'https://your-codex-proxy.example.com' },
          { key: 'codex_proxy_key', label: 'Admin Key', secret: true },
          { key: 'codex_proxy_upload_type', label: 'Upload Type' },
        ],
      },
      {
        title: 'SMSToMe Phone Verification',
        desc: 'Automatically fetches phone numbers and polls SMS codes during ChatGPT add_phone',
        fields: [
          { key: 'smstome_cookie', label: 'SMSToMe Cookie', secret: true },
          { key: 'smstome_country_slugs', label: 'Country List', placeholder: 'united-kingdom,poland' },
          { key: 'smstome_phone_attempts', label: 'Phone Number Attempts', placeholder: '3' },
          { key: 'smstome_otp_timeout_seconds', label: 'SMS Wait Timeout (seconds)', placeholder: '45' },
          { key: 'smstome_poll_interval_seconds', label: 'Polling Interval (seconds)', placeholder: '5' },
          { key: 'smstome_sync_max_pages_per_country', label: 'Pages Synced per Country', placeholder: '5' },
        ],
      },
    ],
  },
  {
    key: 'cliproxyapi',
    label: 'CLIProxyAPI',
    icon: <ApiOutlined />,
    sections: [
      {
        title: 'Management Panel',
        desc: 'Used to sign in to the CLIProxyAPI management page',
        fields: [
          { key: 'cliproxyapi_base_url', label: 'API URL', placeholder: 'http://127.0.0.1:8317' },
          { key: 'cliproxyapi_management_key', label: 'Management Key', secret: true, placeholder: 'Default: cliproxyapi' },
        ],
      },
    ],
  },
  {
    key: 'grok',
    label: 'Grok',
    icon: <ApiOutlined />,
    sections: [
      {
        title: 'grok2api',
        desc: 'Automatically imports successful registrations into grok2api',
        fields: [
          { key: 'grok2api_url', label: 'API URL', placeholder: 'http://127.0.0.1:7860' },
          { key: 'grok2api_app_key', label: 'App Key', secret: true },
          { key: 'grok2api_pool', label: 'Token Pool', placeholder: 'ssoBasic or ssoSuper' },
          { key: 'grok2api_quota', label: 'Quota (Optional)', placeholder: 'Leave empty to use the pool default' },
        ],
      },
    ],
  },
  {
    key: 'kiro',
    label: 'Kiro',
    icon: <ApiOutlined />,
    sections: [
      {
        title: 'Kiro Account Manager',
        desc: 'Automatically writes successful registrations into kiro-account-manager/accounts.json',
        fields: [
          {
            key: 'kiro_manager_path',
            label: 'accounts.json Path (Optional)',
            placeholder: 'Leave empty to use the system default path',
          },
          {
            key: 'kiro_manager_exe',
            label: 'Kiro Manager Executable (Optional)',
            placeholder: 'If Rust is not installed, you can point to an existing KiroAccountManager.exe',
          },
        ],
      },
    ],
  },
  {
    key: 'fotor',
    label: 'Fotor',
    icon: <ApiOutlined />,
    sections: [
      {
        title: 'Referral Flow',
        desc: 'Default referral link and recommended referral cap for Fotor credit farming',
        fields: [
          { key: 'fotor_ref_link', label: 'Referral Link', placeholder: 'https://www.fotor.com/referrer/ce1yh8e7' },
          { key: 'fotor_ref_limit', label: 'Referral Limit', placeholder: '20' },
        ],
      },
    ],
  },
  {
    key: 'integrations',
    label: 'Integrations',
    icon: <ApiOutlined />,
    sections: [],
  },
  {
    key: 'security',
    label: 'Security',
    icon: <LockOutlined />,
    sections: [],
  },
]

interface FieldConfig {
  key: string
  label: string
  placeholder?: string
  type?: 'select' | 'input' | 'boolean'
  secret?: boolean
}

interface SectionConfig {
  title: string
  desc?: string
  fields: FieldConfig[]
  provider?: string
}

interface TabConfig {
  key: string
  label: string
  icon: React.ReactNode
  sections: SectionConfig[]
}

function formatResultText(data: unknown) {
  if (typeof data === 'string') return data
  try {
    return JSON.stringify(data, null, 2)
  } catch {
    return String(data)
  }
}

function normalizeDomainList(input: unknown): string[] {
  const items = Array.isArray(input) ? input : []
  const seen = new Set<string>()
  const domains: string[] = []
  for (const item of items) {
    const domain = String(item || '').trim().toLowerCase().replace(/^@/, '')
    if (!domain || seen.has(domain)) continue
    seen.add(domain)
    domains.push(domain)
  }
  return domains
}

function parseStoredDomainList(value: unknown): string[] {
  if (Array.isArray(value)) return normalizeDomainList(value)
  if (typeof value !== 'string') return []

  const text = value.trim()
  if (!text) return []

  try {
    const parsed = JSON.parse(text)
    if (Array.isArray(parsed)) {
      return normalizeDomainList(parsed)
    }
  } catch {}

  return normalizeDomainList(
    text
      .split('\n')
      .flatMap((line) => line.split(','))
      .map((item) => item.trim()),
  )
}

function ConfigField({ field }: { field: FieldConfig }) {
  const [showSecret, setShowSecret] = useState(false)
  const options = SELECT_FIELDS[field.key]
  const isBooleanField = field.type === 'boolean'
  const helpText =
    field.key === 'default_executor'
      ? 'Only applies to supported platforms. ChatGPT, Cursor, Grok, Kiro, Tavily, and Trae support browser mode, while OpenBlockLabs supports protocol mode only.'
      : undefined

  return (
    <Form.Item
      label={field.label}
      name={field.key}
      extra={helpText}
      valuePropName={isBooleanField ? 'checked' : undefined}
    >
      {options ? (
        <Select options={options} style={{ width: '100%' }} />
      ) : isBooleanField ? (
        <Switch checkedChildren="On" unCheckedChildren="Off" />
      ) : field.secret ? (
        <Input.Password
          placeholder={field.placeholder}
          visibilityToggle={{
            visible: !showSecret,
            onVisibleChange: setShowSecret,
          }}
          iconRender={(visible) => (visible ? <EyeOutlined /> : <EyeInvisibleOutlined />)}
        />
      ) : (
        <Input placeholder={field.placeholder} />
      )}
    </Form.Item>
  )
}

function ConfigSection({ section }: { section: SectionConfig }) {
  return (
    <Card title={section.title} extra={section.desc && <span style={{ fontSize: 12, color: '#7a8ba3' }}>{section.desc}</span>} style={{ marginBottom: 16 }}>
      {section.fields.map((field) => (
        <ConfigField key={field.key} field={field} />
      ))}
    </Card>
  )
}

function MailboxSections({ form, sections }: { form: any; sections: SectionConfig[] }) {
  const selectedProvider = Form.useWatch('mail_provider', form) || 'luckmail'
  const baseSections = sections.filter((section) => !section.provider)
  const providerSections = sections.filter((section) => section.provider)
  const activeProviderSection =
    providerSections.find((section) => section.provider === selectedProvider) || providerSections[0]

  return (
    <>
      {baseSections.map((section) => (
        <ConfigSection key={section.title} section={section} />
      ))}

      {activeProviderSection ? (
        <Card
          title={activeProviderSection.title}
          extra={activeProviderSection.desc && <span style={{ fontSize: 12, color: '#7a8ba3' }}>{activeProviderSection.desc}</span>}
          style={{ marginBottom: 16 }}
        >
          {activeProviderSection.fields.length > 0 ? (
            activeProviderSection.fields.map((field) => <ConfigField key={field.key} field={field} />)
          ) : (
            <Typography.Text type="secondary">The current mailbox provider does not require extra configuration.</Typography.Text>
          )}
        </Card>
      ) : null}
    </>
  )
}

function CFWorkerDomainPoolSection({ form }: { form: any }) {
  const watchedDomains = Form.useWatch('cfworker_domains', form) || []
  const watchedEnabledDomains = Form.useWatch('cfworker_enabled_domains', form) || []
  const normalizedDomains = normalizeDomainList(watchedDomains)
  const enabledDomains = normalizeDomainList(watchedEnabledDomains).filter((domain) => normalizedDomains.includes(domain))

  const updateEnabledDomains = (nextDomains: string[]) => {
    form.setFieldValue('cfworker_enabled_domains', normalizeDomainList(nextDomains))
  }

  const toggleEnabledDomain = (domain: string, checked: boolean) => {
    if (checked) {
      updateEnabledDomains([...enabledDomains, domain])
      return
    }
    updateEnabledDomains(enabledDomains.filter((item) => item !== domain))
  }

  return (
    <Card
      title="CF Worker Domain Pool"
      extra={<span style={{ fontSize: 12, color: '#7a8ba3' }}>Registrations randomly choose one enabled domain</span>}
      style={{ marginBottom: 16 }}
    >
      <Form.List name="cfworker_domains">
        {(fields, { add, remove }) => (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {fields.map((field) => (
              <Space key={field.key} align="start" style={{ display: 'flex' }}>
                <Form.Item
                  {...field}
                  label={field.name === 0 ? 'All Domains' : ''}
                  style={{ flex: 1, marginBottom: 0 }}
                  rules={[
                    {
                      validator: async (_, value) => {
                        if (!String(value || '').trim()) {
                          throw new Error('Enter a domain')
                        }
                      },
                    },
                  ]}
                >
                  <Input placeholder="example.com" />
                </Form.Item>
                <Button
                  danger
                  onClick={() => {
                    const currentDomains = Array.isArray(form.getFieldValue('cfworker_domains'))
                      ? [...form.getFieldValue('cfworker_domains')]
                      : []
                    const removedDomain = String(currentDomains[field.name] || '').trim().toLowerCase().replace(/^@/, '')
                    remove(field.name)
                    if (!removedDomain) return
                    const enabledDomains = normalizeDomainList(form.getFieldValue('cfworker_enabled_domains'))
                    form.setFieldValue(
                      'cfworker_enabled_domains',
                      enabledDomains.filter((domain) => domain !== removedDomain),
                    )
                  }}
                >
                  Delete
                </Button>
              </Space>
            ))}
            {fields.length === 0 ? (
              <Typography.Text type="secondary">No domains configured yet. Add one to enable it below.</Typography.Text>
            ) : null}
            <Button type="dashed" onClick={() => add('')} icon={<PlusOutlined />} block>
              Add Domain
            </Button>
          </div>
        )}
      </Form.List>

      <Form.Item name="cfworker_enabled_domains" hidden>
        <Select mode="multiple" options={normalizedDomains.map((domain) => ({ label: domain, value: domain }))} />
      </Form.Item>

      <div style={{ marginTop: 16 }}>
        <div style={{ marginBottom: 8, fontWeight: 500 }}>Enabled Domains</div>
        {enabledDomains.length > 0 ? (
          <Space wrap>
            {enabledDomains.map((domain) => (
              <Tag
                key={domain}
                color="blue"
                closable
                onClose={(event) => {
                  event.preventDefault()
                  updateEnabledDomains(enabledDomains.filter((item) => item !== domain))
                }}
              >
                {domain}
              </Tag>
            ))}
          </Space>
        ) : (
          <Typography.Text type="secondary">No enabled domains yet. Click a domain below to enable it.</Typography.Text>
        )}
      </div>

      <div style={{ marginTop: 16 }}>
        <div style={{ marginBottom: 8, fontWeight: 500 }}>Click to toggle enabled state</div>
        {normalizedDomains.length > 0 ? (
          <Space wrap>
            {normalizedDomains.map((domain) => (
              <Tag.CheckableTag
                key={domain}
                checked={enabledDomains.includes(domain)}
                onChange={(checked) => toggleEnabledDomain(domain, checked)}
              >
                {domain}
              </Tag.CheckableTag>
            ))}
          </Space>
        ) : (
          <Typography.Text type="secondary">Add domains above first.</Typography.Text>
        )}
      </div>
      <Typography.Text type="secondary" style={{ display: 'block', marginTop: 12 }}>
        Only enabled domains participate in registration. Click an enabled tag to remove it.
      </Typography.Text>
    </Card>
  )
}

function SolverStatus() {
  const [running, setRunning] = useState<boolean | null>(null)

  const checkSolver = async () => {
    try {
      const d = await apiFetch('/solver/status')
      setRunning(d.running)
    } catch {
      setRunning(false)
    }
  }

  const restartSolver = async () => {
    await apiFetch('/solver/restart', { method: 'POST' })
    setRunning(null)
    setTimeout(checkSolver, 2000)
  }

  useEffect(() => {
    checkSolver()
    const timer = window.setInterval(checkSolver, 5000)
    return () => window.clearInterval(timer)
  }, [])

  return (
    <Card title="Turnstile Solver" size="small" style={{ marginBottom: 16 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 12,
          flexWrap: 'wrap',
        }}
      >
        <Space size={8}>
          {running === null ? (
            <SyncOutlined spin style={{ color: '#7a8ba3' }} />
          ) : running ? (
            <CheckCircleOutlined style={{ color: '#10b981' }} />
          ) : (
            <CloseCircleOutlined style={{ color: '#ef4444' }} />
          )}
          <span style={{ color: running ? '#10b981' : '#7a8ba3', fontWeight: 500 }}>
            {running === null ? 'Checking' : running ? 'Running' : 'Stopped'}
          </span>
        </Space>
        <Button size="small" onClick={restartSolver}>
          Restart Solver
        </Button>
      </div>
    </Card>
  )
}

function IntegrationsPanel() {
  const [items, setItems] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState('')
  const [resultModal, setResultModal] = useState({
    open: false,
    title: '',
    ok: true,
    content: '',
  })

  const showResultModal = (title: string, data: unknown, ok = true) => {
    setResultModal({
      open: true,
      title,
      ok,
      content: formatResultText(data),
    })
  }

  const load = async () => {
    setLoading(true)
    try {
      const d = await apiFetch('/integrations/services')
      setItems(d.items || [])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    const timer = window.setInterval(load, 5000)
    return () => window.clearInterval(timer)
  }, [])

  const doAction = async (key: string, request: Promise<any>) => {
    setBusy(key)
    try {
      const result = await request
      await load()
      message.success('Operation completed')
      showResultModal('Operation Result', result, true)
    } catch (e: any) {
      message.error(e?.message || 'Operation failed')
      showResultModal('Operation Result', e?.message || e || 'Operation failed', false)
      await load()
    } finally {
      setBusy('')
    }
  }

  const backfill = async (platforms: string[], label: string, busyKey: string) => {
    setBusy(busyKey)
    try {
      const d = await apiFetch('/integrations/backfill', {
        method: 'POST',
        body: JSON.stringify({ platforms }),
      })
      message.success(`${label} backfill completed: ${d.success} / ${d.total}`)
      showResultModal(`${label} Backfill Result`, d, true)
    } catch (e: any) {
      message.error(e?.message || `${label} backfill failed`)
      showResultModal(`${label} Backfill Result`, e?.message || e || `${label} backfill failed`, false)
    } finally {
      setBusy('')
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Modal
        open={resultModal.open}
        title={resultModal.title}
        onCancel={() => setResultModal((v) => ({ ...v, open: false }))}
        onOk={() => setResultModal((v) => ({ ...v, open: false }))}
        width={760}
      >
        <Typography.Paragraph style={{ marginBottom: 8, color: resultModal.ok ? '#10b981' : '#ef4444' }}>
          {resultModal.ok ? 'Operation completed.' : 'Operation failed.'}
        </Typography.Paragraph>
        <pre
          style={{
            margin: 0,
            maxHeight: 420,
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
          {resultModal.content}
        </pre>
      </Modal>

      <Card title="Bulk Actions">
        <Space wrap>
          <Button loading={busy === 'start-all'} onClick={() => doAction('start-all', apiFetch('/integrations/services/start-all', { method: 'POST' }))}>
            Start All (installed)
          </Button>
          <Button loading={busy === 'stop-all'} onClick={() => doAction('stop-all', apiFetch('/integrations/services/stop-all', { method: 'POST' }))}>
            Stop All
          </Button>
          <Button loading={loading} onClick={load}>
            Refresh Status
          </Button>
        </Space>
      </Card>

      {items.map((item) => (
        <Card key={item.name} title={item.label}>
          <Space direction="vertical" style={{ width: '100%' }}>
            <div>
              Status:
              <Tag color={item.running ? 'green' : 'default'} style={{ marginLeft: 8 }}>
                {item.running ? 'Running' : 'Stopped'}
              </Tag>
              <Tag color={item.repo_exists ? 'blue' : 'orange'} style={{ marginLeft: 8 }}>
                {item.repo_exists ? 'Installed' : 'Not Installed'}
              </Tag>
              {item.pid ? <span style={{ marginLeft: 8 }}>PID: {item.pid}</span> : null}
            </div>
            <div>Plugin Path: <Typography.Text copyable>{item.repo_path}</Typography.Text></div>
            {item.url ? <div>URL: <Typography.Text copyable>{item.url}</Typography.Text></div> : null}
            {item.management_url ? <div>Management Page: <Typography.Text copyable>{item.management_url}</Typography.Text></div> : null}
            {item.management_key ? <div>Login Key: <Typography.Text copyable>{item.management_key}</Typography.Text></div> : null}
            <div>Log Path: <Typography.Text copyable>{item.log_path}</Typography.Text></div>
            {item.last_error ? <div style={{ color: '#ef4444' }}>Latest Error: {item.last_error}</div> : null}
            <Space wrap>
              {item.management_url ? (
                <Button onClick={() => window.open(item.management_url, '_blank')}>
                  Open Management Page
                </Button>
              ) : null}
              {!item.repo_exists ? (
                <Button
                  type="primary"
                  loading={busy === `install-${item.name}`}
                  onClick={() => doAction(`install-${item.name}`, apiFetch(`/integrations/services/${item.name}/install`, { method: 'POST' }))}
                >
                  Install
                </Button>
              ) : null}
              <Button
                loading={busy === `start-${item.name}`}
                disabled={!item.repo_exists}
                onClick={() => doAction(`start-${item.name}`, apiFetch(`/integrations/services/${item.name}/start`, { method: 'POST' }))}
              >
                Start
              </Button>
              <Button
                loading={busy === `stop-${item.name}`}
                onClick={() => doAction(`stop-${item.name}`, apiFetch(`/integrations/services/${item.name}/stop`, { method: 'POST' }))}
              >
                Stop
              </Button>
              {item.name === 'grok2api' ? (
                <Button
                  loading={busy === 'backfill-grok'}
                  onClick={() => backfill(['grok'], 'Grok', 'backfill-grok')}
                >
                  Backfill Existing Grok Accounts
                </Button>
              ) : null}
              {item.name === 'kiro-manager' ? (
                <Button
                  loading={busy === 'backfill-kiro'}
                  onClick={() => backfill(['kiro'], 'Kiro', 'backfill-kiro')}
                >
                  Backfill Existing Kiro Accounts
                </Button>
              ) : null}
            </Space>
          </Space>
        </Card>
      ))}
    </div>
  )
}

type TotpSetupState = 'idle' | 'setup'

function SecurityPanel() {
  const { message: msg } = App.useApp()
  const [status, setStatus] = useState<{ has_password: boolean; has_totp: boolean } | null>(null)
  const [loading, setLoading] = useState(false)

  const [enableForm] = Form.useForm()
  const [pwForm] = Form.useForm()
  const [codeForm] = Form.useForm()

  const [totpSetupState, setTotpSetupState] = useState<TotpSetupState>('idle')
  const [totpSecret, setTotpSecret] = useState('')
  const [totpUri, setTotpUri] = useState('')

  const loadStatus = async () => {
    try {
      const s = await apiFetch('/auth/status')
      setStatus(s)
    } catch {}
  }

  useEffect(() => { loadStatus() }, [])

  const handleEnable = async (values: { password: string; confirm: string }) => {
    if (values.password !== values.confirm) {
      msg.error('The two passwords do not match')
      return
    }
    setLoading(true)
    try {
      const d = await apiFetch('/auth/setup', {
        method: 'POST',
        body: JSON.stringify({ password: values.password }),
      })
      localStorage.setItem('auth_token', d.access_token)
      msg.success('Password protection enabled')
      enableForm.resetFields()
      await loadStatus()
    } catch (e: any) {
      msg.error(e.message)
    } finally {
      setLoading(false)
    }
  }

  const handleDisableAuth = async () => {
    setLoading(true)
    try {
      await apiFetch('/auth/disable', { method: 'POST' })
      localStorage.removeItem('auth_token')
      msg.success('Password protection disabled')
      await loadStatus()
    } catch (e: any) {
      msg.error(e.message)
    } finally {
      setLoading(false)
    }
  }

  const handleChangePassword = async (values: { current_password: string; new_password: string; confirm: string }) => {
    if (values.new_password !== values.confirm) {
      msg.error('The two new passwords do not match')
      return
    }
    setLoading(true)
    try {
      await apiFetch('/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({ current_password: values.current_password, new_password: values.new_password }),
      })
      msg.success('Password updated')
      pwForm.resetFields()
    } catch (e: any) {
      msg.error(e.message)
    } finally {
      setLoading(false)
    }
  }

  const handleSetupTotp = async () => {
    setLoading(true)
    try {
      const d = await apiFetch('/auth/2fa/setup')
      setTotpSecret(d.secret)
      setTotpUri(d.uri)
      setTotpSetupState('setup')
    } catch (e: any) {
      msg.error(e.message)
    } finally {
      setLoading(false)
    }
  }

  const handleEnableTotp = async (values: { code: string }) => {
    setLoading(true)
    try {
      await apiFetch('/auth/2fa/enable', {
        method: 'POST',
        body: JSON.stringify({ secret: totpSecret, code: values.code }),
      })
      msg.success('Two-factor authentication enabled')
      setTotpSetupState('idle')
      codeForm.resetFields()
      await loadStatus()
    } catch (e: any) {
      msg.error(e.message)
    } finally {
      setLoading(false)
    }
  }

  const handleDisableTotp = async () => {
    setLoading(true)
    try {
      await apiFetch('/auth/2fa/disable', { method: 'POST' })
      msg.success('Two-factor authentication disabled')
      await loadStatus()
    } catch (e: any) {
      msg.error(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Card
        title="Access Password Protection"
        extra={
          status?.has_password
            ? <Tag color="green"><CheckCircleOutlined /> Enabled</Tag>
            : <Tag color="default"><CloseCircleOutlined /> Disabled</Tag>
        }
      >
        {!status?.has_password ? (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Typography.Text type="secondary">
              When enabled, visitors must enter a password to access the app. By default this is off, so anyone who can reach the URL can use it.
            </Typography.Text>
            <Form form={enableForm} layout="vertical" onFinish={handleEnable} requiredMark={false} style={{ maxWidth: 360, marginTop: 8 }}>
              <Form.Item name="password" label="Set Access Password" rules={[{ required: true, message: 'Enter a password' }, { min: 6, message: 'At least 6 characters' }]}>
                <Input.Password placeholder="At least 6 characters" />
              </Form.Item>
              <Form.Item name="confirm" label="Confirm Password" rules={[{ required: true, message: 'Enter the password again' }]}>
                <Input.Password placeholder="Enter the password again" />
              </Form.Item>
              <Form.Item style={{ marginBottom: 0 }}>
                <Button type="primary" htmlType="submit" loading={loading} icon={<LockOutlined />}>
                  Enable Password Protection
                </Button>
              </Form.Item>
            </Form>
          </Space>
        ) : (
          <Space direction="vertical" style={{ width: '100%' }}>
            <Typography.Text type="secondary">Password protection is currently enabled. If you disable it, anyone can access the app without a password.</Typography.Text>
            <Button danger loading={loading} onClick={handleDisableAuth}>
              Disable Password Protection
            </Button>
          </Space>
        )}
      </Card>

      {status?.has_password && (
        <>
          <Card title="Change Password">
            <Form form={pwForm} layout="vertical" onFinish={handleChangePassword} requiredMark={false} style={{ maxWidth: 360 }}>
              <Form.Item name="current_password" label="Current Password" rules={[{ required: true, message: 'Enter the current password' }]}>
                <Input.Password placeholder="Current password" />
              </Form.Item>
              <Form.Item name="new_password" label="New Password" rules={[{ required: true, message: 'Enter a new password' }, { min: 6, message: 'At least 6 characters' }]}>
                <Input.Password placeholder="New password (at least 6 characters)" />
              </Form.Item>
              <Form.Item name="confirm" label="Confirm New Password" rules={[{ required: true, message: 'Enter the new password again' }]}>
                <Input.Password placeholder="Enter the new password again" />
              </Form.Item>
              <Form.Item style={{ marginBottom: 0 }}>
                <Button type="primary" htmlType="submit" loading={loading} icon={<SaveOutlined />}>
                  Update Password
                </Button>
              </Form.Item>
            </Form>
          </Card>

          <Card
            title="Two-Factor Authentication (2FA)"
            extra={
              status?.has_totp
                ? <Tag color="green"><CheckCircleOutlined /> Enabled</Tag>
                : <Tag color="default"><CloseCircleOutlined /> Disabled</Tag>
            }
          >
            {status?.has_totp ? (
              <Space direction="vertical">
                <Typography.Text type="secondary">
                  Users must enter a 6-digit code from Google Authenticator, Authy, or a similar app when signing in.
                </Typography.Text>
                <Button danger loading={loading} onClick={handleDisableTotp}>
                  Disable Two-Factor Authentication
                </Button>
              </Space>
            ) : totpSetupState === 'idle' ? (
              <Space direction="vertical">
                <Typography.Text type="secondary">
                  When enabled, signing in requires both the password and a 6-digit code from an authenticator app.
                </Typography.Text>
                <Button type="primary" loading={loading} onClick={handleSetupTotp} icon={<SafetyOutlined />}>
                  Enable Two-Factor Authentication
                </Button>
              </Space>
            ) : (
              <Space direction="vertical" style={{ width: '100%' }}>
                <Typography.Text strong>1. Scan the QR code below with your authenticator app</Typography.Text>
                <div style={{ display: 'flex', gap: 24, alignItems: 'flex-start', flexWrap: 'wrap' }}>
                  <QRCode value={totpUri} size={180} />
                  <div style={{ flex: 1 }}>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>Cannot scan it? Enter the secret manually:</Typography.Text>
                    <Typography.Paragraph copyable style={{ fontFamily: 'monospace', fontSize: 13, marginTop: 4 }}>
                      {totpSecret}
                    </Typography.Paragraph>
                  </div>
                </div>
                <Typography.Text strong>2. Enter the 6-digit code shown in the app to confirm setup</Typography.Text>
                <Form form={codeForm} layout="inline" onFinish={handleEnableTotp}>
                  <Form.Item name="code" rules={[{ required: true, message: 'Enter the verification code' }, { len: 6, message: '6 digits' }]}>
                    <Input placeholder="000000" maxLength={6} style={{ width: 140, letterSpacing: 4, textAlign: 'center' }} />
                  </Form.Item>
                  <Form.Item>
                    <Button type="primary" htmlType="submit" loading={loading}>Confirm</Button>
                  </Form.Item>
                  <Form.Item>
                    <Button onClick={() => setTotpSetupState('idle')}>Cancel</Button>
                  </Form.Item>
                </Form>
              </Space>
            )}
          </Card>
        </>
      )}
    </div>
  )
}

export default function Settings() {
  const [form] = Form.useForm()
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [activeTab, setActiveTab] = useState('register')

  useEffect(() => {
    apiFetch('/config').then((data) => {
      if (!data.mail_provider) {
        data.mail_provider = 'luckmail'
      }
      if (!data.gptmail_base_url) {
        data.gptmail_base_url = 'https://mail.chatgpt.org.uk'
      }
      if (!data.maliapi_base_url) {
        data.maliapi_base_url = 'https://maliapi.215.im/v1'
      }
      if (!data.luckmail_base_url) {
        data.luckmail_base_url = 'https://mails.luckyous.com/'
      }
      data.cfworker_domains = parseStoredDomainList(data.cfworker_domains)
      data.cfworker_enabled_domains = parseStoredDomainList(data.cfworker_enabled_domains)
      data.cfworker_random_subdomain = parseBooleanConfigValue(data.cfworker_random_subdomain)
      form.setFieldsValue(data)
    })
  }, [form])

  const save = async () => {
    setSaving(true)
    try {
      const values = form.getFieldsValue(true)
      const domains = normalizeDomainList(values.cfworker_domains)
      const enabledDomains = normalizeDomainList(values.cfworker_enabled_domains).filter((domain) => domains.includes(domain))

      if (domains.length > 0 && enabledDomains.length === 0) {
        setActiveTab('mailbox')
        message.error('Enable at least one CF Worker domain')
        return
      }

      values.cfworker_domains = JSON.stringify(domains)
      values.cfworker_enabled_domains = JSON.stringify(enabledDomains)
      if (domains.length > 0) {
        values.cfworker_domain = ''
      }
      values.cfworker_random_subdomain = parseBooleanConfigValue(values.cfworker_random_subdomain)

      await apiFetch('/config', { method: 'PUT', body: JSON.stringify({ data: values }) })
      form.setFieldsValue({
        cfworker_domains: domains,
        cfworker_enabled_domains: enabledDomains,
        cfworker_domain: domains.length > 0 ? '' : values.cfworker_domain,
        cfworker_random_subdomain: values.cfworker_random_subdomain,
      })
      message.success('Saved successfully')
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } finally {
      setSaving(false)
    }
  }

  const currentTab = TAB_ITEMS.find((t) => t.key === activeTab) as TabConfig
  const selectedMailProvider = Form.useWatch('mail_provider', form) || 'luckmail'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <h1 style={{ fontSize: 24, fontWeight: 'bold', margin: 0 }}>Settings</h1>
        <p style={{ color: '#7a8ba3', marginTop: 4 }}>Settings are persisted and automatically applied to registration tasks</p>
      </div>

      <div style={{ display: 'flex', gap: 24 }}>
        <div style={{ width: 200 }}>
          <Tabs
            tabPosition="left"
            activeKey={activeTab}
            onChange={setActiveTab}
            items={TAB_ITEMS.map((t) => ({
              key: t.key,
              label: (
                <span>
                  {t.icon}
                  <span style={{ marginLeft: 8 }}>{t.label}</span>
                </span>
              ),
            }))}
          />
        </div>

        <div style={{ flex: 1 }}>
          {activeTab === 'integrations' ? (
            <IntegrationsPanel />
          ) : activeTab === 'security' ? (
            <SecurityPanel />
          ) : (
            <Form form={form} layout="vertical">
              {activeTab === 'captcha' ? <SolverStatus /> : null}
              {activeTab === 'mailbox' ? (
                <>
                  <MailboxSections form={form} sections={currentTab.sections} />
                  {selectedMailProvider === 'cfworker' ? <CFWorkerDomainPoolSection form={form} /> : null}
                </>
              ) : (
                currentTab.sections.map((section) => <ConfigSection key={section.title} section={section} />)
              )}
              <Button type="primary" icon={<SaveOutlined />} onClick={save} loading={saving} block>
                {saved ? 'Saved ✓' : 'Save Settings'}
              </Button>
            </Form>
          )}
        </div>
      </div>
    </div>
  )
}
