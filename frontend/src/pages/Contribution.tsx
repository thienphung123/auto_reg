import { useState, useEffect } from 'react'
import { Card, Switch, Input, Button, Space, Tag, Typography, Form, App, Modal, Spin, Alert, InputNumber } from 'antd'
import {
  SaveOutlined,
  ReloadOutlined,
  WalletOutlined,
  GlobalOutlined,
  KeyOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { apiFetch } from '@/lib/utils'

const { Title, Text, Paragraph } = Typography

export default function ContributionPage() {
  const { message: msg, modal } = App.useApp()
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [config, setConfig] = useState({
    enabled: false,
    api_url: '',
    api_key: '',
  })
  const [quotaStats, setQuotaStats] = useState<any>(null)
  const [keyInfo, setKeyInfo] = useState<any>(null)
  const [redeemAmount, setRedeemAmount] = useState<number | undefined>(undefined)
  const [redeeming, setRedeeming] = useState(false)

  const loadConfig = async () => {
    try {
      const data = await apiFetch('/api/contribution/config')
      setConfig(data)
      form.setFieldsValue(data)
    } catch (error: any) {
      msg.error(`Failed to load configuration: ${error.message}`)
    }
  }

  const loadStats = async () => {
    if (!config.api_url) return

    setRefreshing(true)
    try {
      const [statsData, keyData] = await Promise.all([
        apiFetch('/api/contribution/quota-stats'),
        config.api_key ? apiFetch('/api/contribution/key-info') : Promise.resolve(null),
      ])
      setQuotaStats(statsData)
      setKeyInfo(keyData)
    } catch (error: any) {
      msg.warning(`Failed to load stats: ${error.message}`)
    } finally {
      setRefreshing(false)
    }
  }

  const handleSaveConfig = async (values: any) => {
    setLoading(true)
    try {
      await apiFetch('/api/contribution/config', {
        method: 'POST',
        body: JSON.stringify(values),
      })
      setConfig(values)
      msg.success('Configuration saved')
      setTimeout(loadStats, 500)
    } catch (error: any) {
      msg.error(`Save failed: ${error.message}`)
    } finally {
      setLoading(false)
    }
  }

  const handleGenerateKey = async () => {
    if (!config.api_url) {
      msg.warning('Configure the server URL first')
      return
    }

    Modal.confirm({
      title: 'Generate a New API Key',
      content: 'Generate a new API key and save it to the current configuration?',
      onOk: async () => {
        try {
          const result = await apiFetch('/api/contribution/generate-key', {
            method: 'POST',
          })
          if (result.key) {
            form.setFieldsValue({ api_key: result.key })
            setConfig(prev => ({ ...prev, api_key: result.key }))
            msg.success('A new API key was generated and saved')
            setTimeout(loadStats, 500)
          }
        } catch (error: any) {
          msg.error(`Generation failed: ${error.message}`)
        }
      },
    })
  }

  const handleRedeem = async () => {
    if (!config.api_key) {
      msg.warning('Configure the API key first')
      return
    }

    if (!redeemAmount || redeemAmount <= 0) {
      msg.warning('Enter a valid redeem amount')
      return
    }

    Modal.confirm({
      title: 'Confirm Redemption',
      content: `Redeem $${redeemAmount}?`,
      okText: 'Redeem',
      okType: 'danger',
      onOk: async () => {
        setRedeeming(true)
        try {
          const result = await apiFetch('/api/contribution/redeem', {
            method: 'POST',
            body: JSON.stringify({ amount_usd: redeemAmount }),
          })

          if (result.code) {
            modal.success({
              title: 'Redeem Successful',
              content: (
                <div>
                  <p>Code: <Text copyable strong>{result.code}</Text></p>
                  <p>Redeemed: ${result.redeemed_amount_usd?.toFixed(2)}</p>
                  <p>Remaining Balance: ${result.remaining_balance_usd?.toFixed(2)}</p>
                </div>
              ),
            })
            setTimeout(loadStats, 1000)
          }
        } catch (error: any) {
          msg.error(`Redeem failed: ${error.message}`)
        } finally {
          setRedeeming(false)
        }
      },
    })
  }

  useEffect(() => {
    loadConfig()
  }, [])

  useEffect(() => {
    if (config.api_url) {
      loadStats()
    }
  }, [config.api_url])

  return (
    <div style={{ padding: '24px', maxWidth: '1200px', margin: '0 auto' }}>
      <div style={{ marginBottom: 24 }}>
        <Title level={3} style={{ margin: 0 }}>
          <ThunderboltOutlined style={{ marginRight: 8, color: '#1890ff' }} />
          Contribution Settings
        </Title>
        <Text type="secondary">
          Saved settings are persisted and automatically applied to future registration jobs
        </Text>
      </div>

      <Card
        title="Configuration"
        style={{ marginBottom: 16 }}
        extra={
          <Button
            type="primary"
            icon={<SaveOutlined />}
            loading={loading}
            onClick={() => form.submit()}
          >
            Save
          </Button>
        }
      >
        <Alert
          message="Contribution mode is optional"
          description={
            <div>
              <p style={{ margin: '4px 0', fontWeight: 500 }}>
                Enable it only if you explicitly want successful accounts uploaded to a contribution server.
              </p>
              <p style={{ margin: '4px 0' }}>
                When enabled, automatic uploads to CPA, CodexProxy, and Sub2API are disabled to avoid duplicate reports.
              </p>
              <p style={{ margin: '4px 0' }}>
                If you leave it disabled, the project still works normally and you can keep using your existing upload targets.
              </p>
              <p style={{ margin: '4px 0' }}>
                Relay site: <a href="https://ai.xem8k5.top/" target="_blank" rel="noopener noreferrer">https://ai.xem8k5.top/</a> | Group: 634758974
              </p>
            </div>
          }
          type="info"
          showIcon
          style={{ marginBottom: 24 }}
        />

        <Form
          form={form}
          layout="vertical"
          onFinish={handleSaveConfig}
          initialValues={config}
        >
          <Form.Item label="Enabled" name="enabled" valuePropName="checked">
            <Switch
              checkedChildren="On"
              unCheckedChildren="Off"
              style={{ width: 60 }}
            />
          </Form.Item>

          <Form.Item
            label={<span><span style={{ color: 'red' }}>*</span> Server URL</span>}
            name="api_url"
            rules={[{ required: true, message: 'Enter the server URL' }]}
          >
            <Input
              placeholder="http://new.xem8k5.top:7317/"
              prefix={<GlobalOutlined />}
            />
          </Form.Item>

          <Form.Item
            label="API Key"
            name="api_key"
            extra={
              <Button
                type="link"
                size="small"
                onClick={handleGenerateKey}
                style={{ padding: 0 }}
              >
                No key yet? Generate one
              </Button>
            }
          >
            <Input.Password
              placeholder="Enter the API key"
              prefix={<KeyOutlined />}
            />
          </Form.Item>
        </Form>
      </Card>

      <Card
        title="Information"
        style={{ marginBottom: 16 }}
        extra={
          <Button
            icon={<ReloadOutlined spin={refreshing} />}
            onClick={loadStats}
            loading={refreshing}
            disabled={!config.api_url}
          >
            Refresh
          </Button>
        }
      >
        {refreshing && !quotaStats ? (
          <div style={{ textAlign: 'center', padding: 20 }}>
            <Spin tip="Loading..." />
          </div>
        ) : quotaStats ? (
          <>
            <div style={{ marginBottom: 16 }}>
              <Title level={5}>Server Stats</Title>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {quotaStats.quota_account_count !== undefined && (
                  <Tag color="blue">Accounts: {quotaStats.quota_account_count}</Tag>
                )}
                {quotaStats.quota_total !== undefined && (
                  <Tag color="blue">Total Quota: {quotaStats.quota_total.toFixed(3)}</Tag>
                )}
                {quotaStats.quota_used !== undefined && (
                  <Tag color="orange">Used: {quotaStats.quota_used.toFixed(3)}</Tag>
                )}
                {quotaStats.quota_remaining !== undefined && (
                  <Tag color="green">Remaining: {quotaStats.quota_remaining.toFixed(3)}</Tag>
                )}
                {quotaStats.quota_used_percent !== undefined && (
                  <Tag color="orange">Used %: {quotaStats.quota_used_percent.toFixed(2)}%</Tag>
                )}
                {quotaStats.quota_remaining_percent !== undefined && (
                  <Tag color="green">Remaining %: {quotaStats.quota_remaining_percent.toFixed(2)}%</Tag>
                )}
                {quotaStats.quota_remaining_accounts !== undefined && (
                  <Tag color="purple">Equivalent Accounts Left: {quotaStats.quota_remaining_accounts.toFixed(2)}</Tag>
                )}
              </div>

              {config.api_key && (
                <div style={{ marginTop: 8 }}>
                  <Text strong>API Key</Text>
                  <Text copyable style={{ marginLeft: 8 }}>
                    {config.api_key}
                  </Text>
                </div>
              )}
            </div>

            {keyInfo && (
              <div style={{ marginTop: 16 }}>
                <Title level={5}>Key Info</Title>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                  {keyInfo.balance_usd !== undefined && (
                    <Tag color="blue">Balance: {keyInfo.balance_usd}</Tag>
                  )}
                  {keyInfo.source && (
                    <Tag color="cyan">Source: {keyInfo.source}</Tag>
                  )}
                  {keyInfo.bound_account_count !== undefined && (
                    <Tag color="green">Bound Accounts: {keyInfo.bound_account_count}</Tag>
                  )}
                  {keyInfo.settled_amount_usd !== undefined && (
                    <Tag color="purple">Settled Amount: {keyInfo.settled_amount_usd}</Tag>
                  )}
                </div>
              </div>
            )}
          </>
        ) : (
          <Text type="secondary">
            {config.api_url ? 'No data yet' : 'Configure the server URL first'}
          </Text>
        )}
      </Card>

      <Card title="Redeem" style={{ marginBottom: 16 }}>
        <Space direction="vertical" style={{ width: '100%' }} size="large">
          {keyInfo?.balance_usd !== undefined && (
            <div>
              <Text>Current key balance: </Text>
              <Text strong style={{ color: '#1890ff' }}>
                ${keyInfo.balance_usd}
              </Text>
            </div>
          )}

          <div>
            <div style={{ marginBottom: 8 }}>Redeem Amount</div>
            <InputNumber
              style={{ width: 220 }}
              placeholder="Amount in USD"
              value={redeemAmount}
              onChange={(val) => setRedeemAmount(val || undefined)}
              min={0}
              precision={2}
              prefix="$"
            />
          </div>

          <Button
            type="primary"
            danger
            loading={redeeming}
            disabled={!redeemAmount || redeemAmount <= 0}
            icon={<WalletOutlined />}
            onClick={handleRedeem}
          >
            Confirm Redeem
          </Button>
        </Space>
      </Card>

      <Card>
        <Title level={5}>Notes</Title>
        <Paragraph>
          <ul style={{ paddingLeft: 20 }}>
            <li>When contribution mode is enabled, successful accounts are uploaded automatically to the configured server.</li>
            <li>You can inspect server quota usage and API key details from this page.</li>
            <li>Balance can be converted into redeem codes for transfer or sharing.</li>
            <li>All settings are stored persistently and survive restarts.</li>
          </ul>
        </Paragraph>
      </Card>
    </div>
  )
}
