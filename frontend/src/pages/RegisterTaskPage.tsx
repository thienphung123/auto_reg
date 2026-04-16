import { useEffect, useState } from 'react'
import {
  Card,
  Form,
  Input,
  InputNumber,
  Select,
  Button,
  Checkbox,
  Tag,
  Space,
  Typography,
  Descriptions,
} from 'antd'
import {
  PlayCircleOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
} from '@ant-design/icons'
import { ChatGPTRegistrationModeSwitch } from '@/components/ChatGPTRegistrationModeSwitch'
import { TaskLogPanel } from '@/components/TaskLogPanel'
import { usePersistentChatGPTRegistrationMode } from '@/hooks/usePersistentChatGPTRegistrationMode'
import { parseBooleanConfigValue } from '@/lib/configValueParsers'
import { buildChatGPTRegistrationRequestAdapter } from '@/lib/chatgptRegistrationRequestAdapter'
import { getExecutorOptions, normalizeExecutorForPlatform } from '@/lib/platformExecutorOptions'
import { apiFetch } from '@/lib/utils'

const { Text } = Typography

export default function RegisterTaskPage() {
  const [form] = Form.useForm()
  const [task, setTask] = useState<any>(null)
  const [polling, setPolling] = useState(false)
  const { mode: chatgptRegistrationMode, setMode: setChatgptRegistrationMode } =
    usePersistentChatGPTRegistrationMode()

  useEffect(() => {
    apiFetch('/config').then((cfg) => {
      const currentPlatform = form.getFieldValue('platform') || 'trae'
      form.setFieldsValue({
        executor_type: normalizeExecutorForPlatform(currentPlatform, cfg.default_executor),
        captcha_solver: cfg.default_captcha_solver || 'yescaptcha',
        mail_provider: cfg.mail_provider || 'luckmail',
        yescaptcha_key: cfg.yescaptcha_key || '',
        moemail_api_url: cfg.moemail_api_url || '',
        moemail_api_key: cfg.moemail_api_key || '',
        skymail_api_base: cfg.skymail_api_base || 'https://api.skymail.ink',
        skymail_token: cfg.skymail_token || '',
        skymail_domain: cfg.skymail_domain || '',
        laoudo_auth: cfg.laoudo_auth || '',
        laoudo_email: cfg.laoudo_email || '',
        laoudo_account_id: cfg.laoudo_account_id || '',
        gptmail_base_url: cfg.gptmail_base_url || 'https://mail.chatgpt.org.uk',
        gptmail_api_key: cfg.gptmail_api_key || '',
        gptmail_domain: cfg.gptmail_domain || '',
        opentrashmail_api_url: cfg.opentrashmail_api_url || '',
        opentrashmail_domain: cfg.opentrashmail_domain || '',
        opentrashmail_password: cfg.opentrashmail_password || '',
        maliapi_base_url: cfg.maliapi_base_url || 'https://maliapi.215.im/v1',
        maliapi_api_key: cfg.maliapi_api_key || '',
        maliapi_domain: cfg.maliapi_domain || '',
        maliapi_auto_domain_strategy: cfg.maliapi_auto_domain_strategy || 'balanced',
        duckmail_api_url: cfg.duckmail_api_url || '',
        duckmail_provider_url: cfg.duckmail_provider_url || '',
        duckmail_bearer: cfg.duckmail_bearer || '',
        freemail_api_url: cfg.freemail_api_url || '',
        freemail_admin_token: cfg.freemail_admin_token || '',
        freemail_username: cfg.freemail_username || '',
        freemail_password: cfg.freemail_password || '',
        cfworker_api_url: cfg.cfworker_api_url || '',
        cfworker_admin_token: cfg.cfworker_admin_token || '',
        cfworker_custom_auth: cfg.cfworker_custom_auth || '',
        cfworker_domain_override: '',
        cfworker_subdomain: cfg.cfworker_subdomain || '',
        cfworker_random_subdomain: parseBooleanConfigValue(cfg.cfworker_random_subdomain),
        cfworker_fingerprint: cfg.cfworker_fingerprint || '',
        smstome_cookie: cfg.smstome_cookie || '',
        smstome_country_slugs: cfg.smstome_country_slugs || '',
        smstome_phone_attempts: cfg.smstome_phone_attempts || '',
        smstome_otp_timeout_seconds: cfg.smstome_otp_timeout_seconds || '',
        smstome_poll_interval_seconds: cfg.smstome_poll_interval_seconds || '',
        smstome_sync_max_pages_per_country: cfg.smstome_sync_max_pages_per_country || '',
        luckmail_base_url: cfg.luckmail_base_url || 'https://mails.luckyous.com/',
        luckmail_api_key: cfg.luckmail_api_key || '',
        luckmail_email_type: cfg.luckmail_email_type || '',
        luckmail_domain: cfg.luckmail_domain || '',
        cpa_api_url: cfg.cpa_api_url || '',
        cpa_api_key: cfg.cpa_api_key || '',
        sub2api_api_url: cfg.sub2api_api_url || '',
        sub2api_api_key: cfg.sub2api_api_key || '',
        sub2api_group_ids: cfg.sub2api_group_ids || '',
        codex_proxy_url: cfg.codex_proxy_url || '',
        codex_proxy_key: cfg.codex_proxy_key || '',
        codex_proxy_upload_type: cfg.codex_proxy_upload_type || 'at',
        team_manager_url: cfg.team_manager_url || '',
        team_manager_key: cfg.team_manager_key || '',
      })
    })
  }, [form])

  const submit = async () => {
    const values = await form.validateFields()
    const registerExtra = {
      mail_provider: values.mail_provider,
      laoudo_auth: values.laoudo_auth,
      laoudo_email: values.laoudo_email,
      laoudo_account_id: values.laoudo_account_id,
      gptmail_base_url: values.gptmail_base_url,
      gptmail_api_key: values.gptmail_api_key,
      gptmail_domain: values.gptmail_domain,
      opentrashmail_api_url: values.opentrashmail_api_url,
      opentrashmail_domain: values.opentrashmail_domain,
      opentrashmail_password: values.opentrashmail_password,
      maliapi_base_url: values.maliapi_base_url,
      maliapi_api_key: values.maliapi_api_key,
      maliapi_domain: values.maliapi_domain,
      maliapi_auto_domain_strategy: values.maliapi_auto_domain_strategy,
      moemail_api_url: values.moemail_api_url,
      moemail_api_key: values.moemail_api_key,
      skymail_api_base: values.skymail_api_base,
      skymail_token: values.skymail_token,
      skymail_domain: values.skymail_domain,
      duckmail_api_url: values.duckmail_api_url,
      duckmail_provider_url: values.duckmail_provider_url,
      duckmail_bearer: values.duckmail_bearer,
      freemail_api_url: values.freemail_api_url,
      freemail_admin_token: values.freemail_admin_token,
      freemail_username: values.freemail_username,
      freemail_password: values.freemail_password,
      cfworker_api_url: values.cfworker_api_url,
      cfworker_admin_token: values.cfworker_admin_token,
      cfworker_custom_auth: values.cfworker_custom_auth,
      cfworker_domain_override: values.cfworker_domain_override,
      cfworker_subdomain: values.cfworker_subdomain,
      cfworker_random_subdomain: values.cfworker_random_subdomain,
      cfworker_fingerprint: values.cfworker_fingerprint,
      smstome_cookie: values.smstome_cookie,
      smstome_country_slugs: values.smstome_country_slugs,
      smstome_phone_attempts: values.smstome_phone_attempts,
      smstome_otp_timeout_seconds: values.smstome_otp_timeout_seconds,
      smstome_poll_interval_seconds: values.smstome_poll_interval_seconds,
      smstome_sync_max_pages_per_country: values.smstome_sync_max_pages_per_country,
      luckmail_base_url: values.luckmail_base_url,
      luckmail_api_key: values.luckmail_api_key,
      luckmail_email_type: values.luckmail_email_type,
      luckmail_domain: values.luckmail_domain,
      yescaptcha_key: values.yescaptcha_key,
      solver_url: values.solver_url,
      cpa_api_url: values.cpa_api_url,
      cpa_api_key: values.cpa_api_key,
      sub2api_api_url: values.sub2api_api_url,
      sub2api_api_key: values.sub2api_api_key,
      sub2api_group_ids: values.sub2api_group_ids,
      codex_proxy_url: values.codex_proxy_url,
      codex_proxy_key: values.codex_proxy_key,
      codex_proxy_upload_type: values.codex_proxy_upload_type,
      team_manager_url: values.team_manager_url,
      team_manager_key: values.team_manager_key,
    }
    const chatgptRegistrationRequestAdapter =
      buildChatGPTRegistrationRequestAdapter(
        values.platform,
        chatgptRegistrationMode,
      )
    const adaptedRegisterExtra = chatgptRegistrationRequestAdapter
      ? chatgptRegistrationRequestAdapter.extendExtra(registerExtra)
      : registerExtra

    const res = await apiFetch('/tasks/register', {
      method: 'POST',
      body: JSON.stringify({
        platform: values.platform,
        email: values.email || null,
        password: values.password || null,
        count: values.count,
        concurrency: values.concurrency,
        register_delay_seconds: values.register_delay_seconds || 0,
        proxy: values.proxy || null,
        executor_type: values.executor_type,
        captcha_solver: values.captcha_solver,
        extra: adaptedRegisterExtra,
      }),
    })
    setTask(res)
    setPolling(true)
    pollTask(res.task_id)
  }

  const pollTask = async (id: string) => {
    const interval = setInterval(async () => {
      const t = await apiFetch(`/tasks/${id}`)
      setTask(t)
      if (t.status === 'done' || t.status === 'failed' || t.status === 'stopped') {
        clearInterval(interval)
        setPolling(false)
        if (t.cashier_urls && t.cashier_urls.length > 0) {
          t.cashier_urls.forEach((url: string) => window.open(url, '_blank'))
        }
      }
    }, 2000)
  }

  const mailProvider = Form.useWatch('mail_provider', form)
  const captchaSolver = Form.useWatch('captcha_solver', form)
  const platform = Form.useWatch('platform', form)
  const executorOptions = getExecutorOptions(platform)

  useEffect(() => {
    const currentExecutor = form.getFieldValue('executor_type')
    const normalizedExecutor = normalizeExecutorForPlatform(platform, currentExecutor)
    if (currentExecutor !== normalizedExecutor) {
      form.setFieldValue('executor_type', normalizedExecutor)
    }
  }, [form, platform])

  return (
    <div style={{ maxWidth: 800 }}>
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontSize: 24, fontWeight: 'bold', margin: 0 }}>Registration Task</h1>
        <p style={{ color: '#7a8ba3', marginTop: 4 }}>Create automated account registration jobs</p>
      </div>

      <Form form={form} layout="vertical" onFinish={submit} initialValues={{
        platform: 'trae',
        executor_type: 'protocol',
        captcha_solver: 'yescaptcha',
        mail_provider: 'luckmail',
        gptmail_base_url: 'https://mail.chatgpt.org.uk',
        count: 1,
        concurrency: 1,
        register_delay_seconds: 0,
        maliapi_base_url: 'https://maliapi.215.im/v1',
        maliapi_auto_domain_strategy: 'balanced',
        solver_url: 'http://localhost:8889',
      }}>
        <Card title="Basic Settings" style={{ marginBottom: 16 }}>
          <Form.Item name="platform" label="Platform" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'chatgpt', label: 'ChatGPT' },
                { value: 'trae', label: 'Trae.ai' },
                { value: 'cursor', label: 'Cursor' },
                { value: 'kiro', label: 'Kiro' },
                { value: 'grok', label: 'Grok' },
                { value: 'tavily', label: 'Tavily' },
                { value: 'openblocklabs', label: 'OpenBlockLabs' },
              ]}
            />
          </Form.Item>
          <Form.Item name="executor_type" label="Executor" rules={[{ required: true }]}>
            <Select options={executorOptions} />
          </Form.Item>
          <Form.Item name="captcha_solver" label="Captcha Solver" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'yescaptcha', label: 'YesCaptcha' },
                { value: 'local_solver', label: 'Local Solver (Camoufox)' },
                { value: 'manual', label: 'Manual' },
              ]}
            />
          </Form.Item>
          <Space style={{ width: '100%' }}>
            <Form.Item name="count" label="Batch Size" style={{ flex: 1 }}>
              <Input type="number" min={1} />
            </Form.Item>
            <Form.Item name="concurrency" label="Concurrency" style={{ flex: 1 }}>
              <Input type="number" min={1} max={5} />
            </Form.Item>
          </Space>
          <Space style={{ width: '100%' }}>
            <Form.Item name="register_delay_seconds" label="Delay per Registration (seconds)" style={{ flex: 1 }}>
              <InputNumber min={0} precision={1} step={0.5} style={{ width: '100%' }} placeholder="0" />
            </Form.Item>
            <Form.Item name="proxy" label="Proxy (Optional)" style={{ flex: 1 }}>
              <Input placeholder="http://user:pass@host:port" />
            </Form.Item>
          </Space>
          {platform === 'chatgpt' && (
            <Form.Item label="ChatGPT Token Mode">
              <ChatGPTRegistrationModeSwitch
                mode={chatgptRegistrationMode}
                onChange={setChatgptRegistrationMode}
              />
            </Form.Item>
          )}
        </Card>

        <Card title="Mailbox Settings" style={{ marginBottom: 16 }}>
          <Form.Item name="mail_provider" label="Mailbox Provider" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'luckmail', label: 'LuckMail' },
                { value: 'moemail', label: 'MoeMail (sall.cc)' },
                { value: 'tempmail_lol', label: 'TempMail.lol' },
                { value: 'skymail', label: 'SkyMail (CloudMail)' },
                { value: 'maliapi', label: 'YYDS Mail / MaliAPI' },
                { value: 'gptmail', label: 'GPTMail' },
                { value: 'opentrashmail', label: 'OpenTrashMail' },
                { value: 'duckmail', label: 'DuckMail' },
                { value: 'freemail', label: 'Freemail' },
                { value: 'laoudo', label: 'Laoudo' },
                { value: 'cfworker', label: 'CF Worker' },
              ]}
            />
          </Form.Item>
          {mailProvider === 'skymail' && (
            <>
              <Form.Item name="skymail_api_base" label="API Base">
                <Input placeholder="https://api.skymail.ink" />
              </Form.Item>
              <Form.Item name="skymail_token" label="Authorization Token">
                <Input.Password placeholder="Bearer xxxxx" />
              </Form.Item>
              <Form.Item name="skymail_domain" label="Mailbox Domain">
                <Input placeholder="mail.example.com" />
              </Form.Item>
            </>
          )}
          {mailProvider === 'laoudo' && (
            <>
              <Form.Item name="laoudo_email" label="Email Address">
                <Input placeholder="xxx@laoudo.com" />
              </Form.Item>
              <Form.Item name="laoudo_account_id" label="Account ID">
                <Input placeholder="563" />
              </Form.Item>
              <Form.Item name="laoudo_auth" label="JWT Token">
                <Input placeholder="eyJ..." />
              </Form.Item>
            </>
          )}
          {mailProvider === 'maliapi' && (
            <>
              <Form.Item name="maliapi_base_url" label="API URL">
                <Input placeholder="https://maliapi.215.im/v1" />
              </Form.Item>
              <Form.Item name="maliapi_api_key" label="API Key">
                <Input.Password placeholder="AC-..." />
              </Form.Item>
              <Form.Item name="maliapi_domain" label="Mailbox Domain (Optional)">
                <Input placeholder="example.com" />
              </Form.Item>
              <Form.Item name="maliapi_auto_domain_strategy" label="Auto Domain Strategy">
                <Select
                  options={[
                    { value: 'balanced', label: 'balanced' },
                    { value: 'prefer_owned', label: 'prefer_owned' },
                    { value: 'prefer_public', label: 'prefer_public' },
                  ]}
                />
              </Form.Item>
            </>
          )}
          {mailProvider === 'gptmail' && (
            <>
              <Form.Item name="gptmail_base_url" label="API URL">
                <Input placeholder="https://mail.chatgpt.org.uk" />
              </Form.Item>
              <Form.Item name="gptmail_api_key" label="API Key">
                <Input.Password placeholder="gpt-test" />
              </Form.Item>
              <Form.Item
                name="gptmail_domain"
                label="Mailbox Domain (Optional)"
                extra="If you already know an available domain, the app can build a random address locally and skip one generate-email request."
              >
                <Input placeholder="example.com" />
              </Form.Item>
            </>
          )}
          {mailProvider === 'opentrashmail' && (
            <>
              <Form.Item name="opentrashmail_api_url" label="API URL" rules={[{ required: true, message: 'Enter the OpenTrashMail URL' }]}>
                <Input placeholder="http://mail.example.com:8085" />
              </Form.Item>
              <Form.Item
                name="opentrashmail_domain"
                label="Mailbox Domain (Optional)"
                extra="If you know the active OpenTrashMail domain, the app can build a random address locally. Leave empty to fetch one from /api/random."
              >
                <Input placeholder="xiyoufm.com" />
              </Form.Item>
              <Form.Item
                name="opentrashmail_password"
                label="Site Password (Optional)"
                extra="Provide this only when PASSWORD protection is enabled for the OpenTrashMail instance."
              >
                <Input.Password placeholder="Leave empty if disabled" />
              </Form.Item>
            </>
          )}
          {mailProvider === 'cfworker' && (
            <>
              <Form.Item name="cfworker_api_url" label="API URL">
                <Input placeholder="https://apimail.example.com" />
              </Form.Item>
              <Form.Item name="cfworker_admin_token" label="Admin Token">
                <Input placeholder="abc123,,,abc" />
              </Form.Item>
              <Form.Item name="cfworker_custom_auth" label="Site Password">
                <Input.Password placeholder="private site password" />
              </Form.Item>
              <Form.Item
                name="cfworker_domain_override"
                label="Per-Task Domain Override (Optional)"
                extra="When left empty, one of the enabled domains from Settings is selected at random."
              >
                <Input placeholder="example.com" />
              </Form.Item>
              <Form.Item
                name="cfworker_subdomain"
                label="Subdomain (Optional)"
                extra="When set, addresses use xxx@subdomain.rootdomain. Random subdomain mode adds another random level in front."
              >
                <Input placeholder="mail / pool-a" />
              </Form.Item>
              <Form.Item name="cfworker_random_subdomain" label="Random Subdomain" valuePropName="checked">
                <Checkbox>Generate one extra random subdomain for each registration</Checkbox>
              </Form.Item>
              <Form.Item name="cfworker_fingerprint" label="Fingerprint (Optional)">
                <Input placeholder="cfb82279f..." />
              </Form.Item>
            </>
          )}
          {mailProvider === 'luckmail' && (
            <>
              <Form.Item name="luckmail_base_url" label="Platform URL">
                <Input placeholder="https://mails.luckyous.com" />
              </Form.Item>
              <Form.Item name="luckmail_api_key" label="API Key">
                <Input.Password placeholder="ak_..." />
              </Form.Item>
              <Form.Item name="luckmail_email_type" label="Mailbox Type (Optional)">
                <Input placeholder="ms_graph / ms_imap" />
              </Form.Item>
              <Form.Item name="luckmail_domain" label="Mailbox Domain (Optional)">
                <Input placeholder="outlook.com" />
              </Form.Item>
            </>
          )}
        </Card>

        {platform === 'chatgpt' && (
          <Card title="ChatGPT Phone Verification" style={{ marginBottom: 16 }}>
            <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>
              Used only when the OAuth flow reaches `add_phone`, to fetch a number automatically and poll for the SMS code.
            </Text>
            <Form.Item name="smstome_cookie" label="SMSToMe Cookie">
              <Input.Password placeholder="cf_clearance=...; PHPSESSID=..." />
            </Form.Item>
            <Form.Item name="smstome_country_slugs" label="Country List">
              <Input placeholder="united-kingdom,poland,finland" />
            </Form.Item>
            <Form.Item name="smstome_phone_attempts" label="Phone Number Attempts">
              <Input placeholder="3" />
            </Form.Item>
            <Form.Item name="smstome_otp_timeout_seconds" label="SMS Wait Timeout (seconds)">
              <Input placeholder="45" />
            </Form.Item>
            <Form.Item name="smstome_poll_interval_seconds" label="Polling Interval (seconds)">
              <Input placeholder="5" />
            </Form.Item>
            <Form.Item name="smstome_sync_max_pages_per_country" label="Pages Synced per Country">
              <Input placeholder="5" />
            </Form.Item>
          </Card>
        )}

        {platform === 'chatgpt' && (
          <Card title="Auto Upload Settings" style={{ marginBottom: 16 }}>
            <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>
              Automatically upload successful registrations to external management platforms. Leave empty to disable.
            </Text>

            <Form.Item name="cpa_api_url" label="CPA API URL">
              <Input placeholder="https://your-cpa.example.com" />
            </Form.Item>
            <Form.Item name="cpa_api_key" label="CPA API Key">
              <Input.Password placeholder="Bearer token" />
            </Form.Item>

            <Form.Item name="sub2api_api_url" label="Sub2API API URL">
              <Input placeholder="https://your-sub2api.example.com" />
            </Form.Item>
            <Form.Item name="sub2api_api_key" label="Sub2API API Key">
              <Input.Password placeholder="API Key" />
            </Form.Item>
            <Form.Item name="sub2api_group_ids" label="Sub2API Group IDs">
              <Input placeholder="Comma-separated, for example 2,4,8" />
            </Form.Item>

            <Form.Item name="codex_proxy_url" label="CodexProxy API URL">
              <Input placeholder="https://your-codex-proxy.example.com" />
            </Form.Item>
            <Form.Item name="codex_proxy_key" label="CodexProxy Admin Key">
              <Input.Password placeholder="Admin Key" />
            </Form.Item>
            <Form.Item name="codex_proxy_upload_type" label="CodexProxy Upload Type">
              <Select
                options={[
                  { value: 'at', label: 'AT (Access Token, Recommended)' },
                  { value: 'rt', label: 'RT (Refresh Token)' },
                ]}
              />
            </Form.Item>

            <Form.Item name="team_manager_url" label="Team Manager API URL">
              <Input placeholder="https://your-tm.example.com" />
            </Form.Item>
            <Form.Item name="team_manager_key" label="Team Manager API Key">
              <Input.Password placeholder="API Key" />
            </Form.Item>
          </Card>
        )}

        {captchaSolver === 'yescaptcha' && (
          <Card title="Captcha Settings" style={{ marginBottom: 16 }}>
            <Form.Item name="yescaptcha_key" label="YesCaptcha Key">
              <Input />
            </Form.Item>
          </Card>
        )}

        {captchaSolver === 'local_solver' && (
          <Card title="Local Solver Settings" style={{ marginBottom: 16 }}>
            <Form.Item name="solver_url" label="Solver URL">
              <Input />
            </Form.Item>
            <Text type="secondary" style={{ fontSize: 12 }}>
              Start command: python services/turnstile_solver/start.py --browser_type camoufox --port 8889
            </Text>
          </Card>
        )}

        <Button type="primary" htmlType="submit" block disabled={polling} icon={polling ? <LoadingOutlined /> : <PlayCircleOutlined />}>
          {polling ? 'Registering...' : 'Start Registration'}
        </Button>
      </Form>

      {task && (
        <Card title={
          <Space>
            <span>Task Status</span>
            <Tag color={
              task.status === 'done' ? 'success' :
              task.status === 'stopped' ? 'warning' :
              task.status === 'failed' ? 'error' : 'processing'
            }>
              {task.status}
            </Tag>
          </Space>
        } style={{ marginTop: 16 }}>
          <Descriptions column={1} size="small">
            <Descriptions.Item label="Task ID">
              <Text copyable style={{ fontFamily: 'monospace' }}>{task.id}</Text>
            </Descriptions.Item>
            <Descriptions.Item label="Progress">{task.progress}</Descriptions.Item>
            <Descriptions.Item label="Skipped">{task.skipped ?? 0}</Descriptions.Item>
          </Descriptions>
          {task.success != null && (
            <div style={{ marginTop: 8, color: '#10b981' }}>
              <CheckCircleOutlined /> Success: {task.success}
            </div>
          )}
          {task.errors?.length > 0 && (
            <div style={{ marginTop: 8 }}>
              {task.errors.map((e: string, i: number) => (
                <div key={i} style={{ color: '#ef4444', marginBottom: 4 }}>
                  <CloseCircleOutlined /> {e}
                </div>
              ))}
            </div>
          )}
          {task.error && (
            <div style={{ marginTop: 8, color: '#ef4444' }}>
              <CloseCircleOutlined /> {task.error}
            </div>
          )}
          {task.id ? (
            <div style={{ marginTop: 16 }}>
              <TaskLogPanel taskId={task.id} />
            </div>
          ) : null}
        </Card>
      )}
    </div>
  )
}
