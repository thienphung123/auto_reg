import { useEffect, useState } from 'react'
import { Card, Table, Button, Input, Tag, Space, Popconfirm, message, Typography } from 'antd'
import {
  PlusOutlined,
  DeleteOutlined,
  ReloadOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  SwapRightOutlined,
  SwapLeftOutlined,
  ClearOutlined,
  CopyOutlined,
} from '@ant-design/icons'
import { apiFetch } from '@/lib/utils'

const { Title, Paragraph, Text } = Typography

export default function Proxies() {
  const [proxies, setProxies] = useState<any[]>([])
  const [quickPaste, setQuickPaste] = useState('')
  const [newProxy, setNewProxy] = useState('')
  const [region, setRegion] = useState('')
  const [checking, setChecking] = useState(false)
  const [loading, setLoading] = useState(false)

  // Pagination state
  const [current, setCurrent] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const [total, setTotal] = useState(0)
  const [activeCount, setActiveCount] = useState(0)

  const load = async (page = current, size = pageSize) => {
    setLoading(true)
    try {
      const resp = await apiFetch(`/proxies?page=${page}&page_size=${size}`)
      setProxies(resp.items || [])
      setTotal(resp.total || 0)
      setActiveCount(resp.active_count || 0)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load(1, pageSize)
  }, [])

  const add = async () => {
    if (!newProxy.trim()) return
    const lines = newProxy.trim().split('\n').map((l) => l.trim()).filter(Boolean)
    try {
      if (lines.length > 1) {
        await apiFetch('/proxies/bulk', {
          method: 'POST',
          body: JSON.stringify({ proxies: lines, region }),
        })
      } else {
        await apiFetch('/proxies', {
          method: 'POST',
          body: JSON.stringify({ url: lines[0], region }),
        })
      }
      message.success('Proxy added successfully')
      setNewProxy('')
      setRegion('')
      load(1, pageSize)
      setCurrent(1)
    } catch (e: any) {
      message.error(`Failed to add proxy: ${e.message}`)
    }
  }

  const bulkAddWebshare = async () => {
    if (!quickPaste.trim()) return
    const lines = quickPaste.trim().split('\n').map((l) => l.trim()).filter(Boolean)
    try {
      const res = await apiFetch('/proxies/bulk-webshare', {
        method: 'POST',
        body: JSON.stringify({ proxies: lines, region }),
      })
      message.success(`Successfully imported ${res.added} proxies`)
      setQuickPaste('')
      setRegion('')
      load(1, pageSize)
      setCurrent(1)
    } catch (e: any) {
      message.error(`Import failed: ${e.message}`)
    }
  }

  const clearAll = async () => {
    try {
      await apiFetch('/proxies/clear-all', { method: 'DELETE' })
      message.success('All proxies cleared')
      load(1, pageSize)
      setCurrent(1)
    } catch (e: any) {
      message.error(`Failed to clear proxies: ${e.message}`)
    }
  }

  const clearDisabled = async () => {
    try {
      await apiFetch('/proxies/clear-disabled', { method: 'DELETE' })
      message.success('All disabled proxies cleared')
      load(1, pageSize) // Luôn reload về trang 1 sau khi xóa diện rộng
      setCurrent(1)
    } catch (e: any) {
      message.error(`Failed to clear disabled proxies: ${e.message}`)
    }
  }

  const del = async (id: number) => {
    await apiFetch(`/proxies/${id}`, { method: 'DELETE' })
    message.success('Proxy deleted')
    // Nếu trang hiện tại không còn item nào (trừ khi là trang 1), thì lùi 1 trang
    const newPage = (proxies.length === 1 && current > 1) ? current - 1 : current
    load(newPage, pageSize)
    if (newPage !== current) setCurrent(newPage)
  }

  const toggle = async (id: number) => {
    await apiFetch(`/proxies/${id}/toggle`, { method: 'PATCH' })
    load(current, pageSize)
  }

  const check = async () => {
    setChecking(true)
    try {
      await apiFetch('/proxies/check', { method: 'POST' })
      message.info('Check task started in background')
      setTimeout(() => {
        load(current, pageSize)
        setChecking(false)
      }, 3000)
    } catch (e: any) {
      message.error('Failed to start check task')
      setChecking(false)
    }
  }

  const columns: any[] = [
    {
      title: 'Proxy URL',
      dataIndex: 'url',
      key: 'url',
      render: (text: string) => <Text code style={{ fontSize: 12 }}>{text}</Text>,
    },
    {
      title: 'Region',
      dataIndex: 'region',
      key: 'region',
      render: (text: string) => text ? <Tag color="blue">{text}</Tag> : '-',
    },
    {
      title: 'Success / Failed',
      key: 'stats',
      render: (_: any, record: any) => (
        <Space>
          <Tag color="success">{record.success_count}</Tag>
          <Tag color="error">{record.fail_count}</Tag>
        </Space>
      ),
    },
    {
      title: 'Status',
      dataIndex: 'is_active',
      key: 'is_active',
      render: (active: boolean) => (
        <Tag color={active ? 'success' : 'error'} icon={active ? <CheckCircleOutlined /> : <CloseCircleOutlined />}>
          {active ? 'Active' : 'Disabled'}
        </Tag>
      ),
    },
    {
      title: 'Actions',
      key: 'action',
      render: (_: any, record: any) => (
        <Space>
          <Button
            type="text"
            size="small"
            icon={record.is_active ? <SwapLeftOutlined /> : <SwapRightOutlined />}
            onClick={() => toggle(record.id)}
            title={record.is_active ? 'Disable' : 'Enable'}
          />
          <Popconfirm title="Delete this proxy?" onConfirm={() => del(record.id)}>
            <Button type="text" size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 1200, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <Title level={2} style={{ margin: 0 }}>Proxy Management</Title>
          <Paragraph type="secondary">
            Total configured: <Text strong>{total}</Text> | Active: <Text type="success" strong>{activeCount}</Text>
          </Paragraph>
        </div>
        <Space>
          <Popconfirm
            title="Clear all disabled proxies?"
            description="This will remove all proxies marked as Disabled. Continue?"
            onConfirm={clearDisabled}
            okText="Clear Disabled"
            cancelText="Cancel"
            okButtonProps={{ danger: true }}
          >
            <Button icon={<DeleteOutlined />} style={{ borderColor: '#faad14', color: '#faad14' }}>
              Xóa Proxy chết
            </Button>
          </Popconfirm>
          <Popconfirm
            title="Clear all proxies?"
            description="This will permanently delete ALL configured proxies. Proceed?"
            onConfirm={clearAll}
            okText="Clear All"
            cancelText="Cancel"
            okButtonProps={{ danger: true }}
          >
            <Button danger icon={<ClearOutlined />}>
              Clear All
            </Button>
          </Popconfirm>
          <Button type="primary" icon={<ReloadOutlined spin={checking} />} onClick={check} loading={checking}>
            Check All
          </Button>
        </Space>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        <Card title={<span><CopyOutlined /> Quick Paste (Webshare Format)</span>} extra={<Text type="secondary" style={{ fontSize: 12 }}>IP:PORT:USER:PASS</Text>}>
          <Space direction="vertical" style={{ width: '100%' }}>
            <Input.TextArea
              value={quickPaste}
              onChange={(e) => setQuickPaste(e.target.value)}
              placeholder="Paste list here (one per line):&#10;191.96.254.138:6185:hwtpryiw:dua...&#10;185.197.83.15:5823:userabc:pass123"
              rows={8}
              style={{ fontFamily: 'monospace', fontSize: 13 }}
            />
            <div style={{ display: 'flex', gap: 8 }}>
              <Input
                value={region}
                onChange={(e) => setRegion(e.target.value)}
                placeholder="Region (Optional)"
                style={{ flex: 1 }}
              />
              <Button type="primary" onClick={bulkAddWebshare} disabled={!quickPaste.trim()}>
                Import Proxies
              </Button>
            </div>
          </Space>
        </Card>

        <Card title={<span><PlusOutlined /> Standard Format</span>} extra={<Text type="secondary" style={{ fontSize: 12 }}>http://user:pass@host:port</Text>}>
          <Space direction="vertical" style={{ width: '100%' }}>
            <Input.TextArea
              value={newProxy}
              onChange={(e) => setNewProxy(e.target.value)}
              placeholder="http://user:pass@ip:port&#10;socks5://user:pass@ip:port"
              rows={8}
              style={{ fontFamily: 'monospace', fontSize: 13 }}
            />
            <Button block onClick={add} disabled={!newProxy.trim()}>
              Add Manually
            </Button>
          </Space>
        </Card>
      </div>

      <Card>
        <Table
          rowKey="id"
          columns={columns}
          dataSource={proxies}
          loading={loading}
          pagination={{
            current,
            pageSize,
            total,
            onChange: (page, size) => {
              setCurrent(page)
              setPageSize(size)
              load(page, size)
            },
            showSizeChanger: true,
          }}
          size="small"
        />
      </Card>
    </div>
  )
}
