import { useCallback, useEffect, useState } from 'react'
import { Card, Table, Select, Button, Tag, Space, Popconfirm, Typography, message } from 'antd'
import type { TableColumnsType } from 'antd'
import { ReloadOutlined, DeleteOutlined } from '@ant-design/icons'
import { apiFetch } from '@/lib/utils'

const { Text } = Typography

interface TaskLogItem {
  id: number
  created_at: string
  platform: string
  email: string
  status: 'success' | 'failed'
  error: string
}

interface TaskLogListResponse {
  total: number
  items: TaskLogItem[]
}

interface TaskLogBatchDeleteResponse {
  deleted: number
  not_found: number[]
  total_requested: number
}

export default function TaskHistory() {
  const [logs, setLogs] = useState<TaskLogItem[]>([])
  const [total, setTotal] = useState(0)
  const [platform, setPlatform] = useState('')
  const [loading, setLoading] = useState(false)
  const [selectedRowKeys, setSelectedRowKeys] = useState<number[]>([])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ page: '1', page_size: '50' })
      if (platform) params.set('platform', platform)
      const data = await apiFetch(`/tasks/logs?${params}`) as TaskLogListResponse
      setLogs(data.items || [])
      setTotal(data.total || 0)
      setSelectedRowKeys((prev) => prev.filter((key) => data.items.some((item) => item.id === key)))
    } finally {
      setLoading(false)
    }
  }, [platform])

  useEffect(() => {
    load()
  }, [load])

  const handleBatchDelete = async () => {
    if (selectedRowKeys.length === 0) return

    const result = await apiFetch('/tasks/logs/batch-delete', {
      method: 'POST',
      body: JSON.stringify({ ids: selectedRowKeys }),
    }) as TaskLogBatchDeleteResponse

    message.success(`Deleted ${result.deleted} task history entries`)
    if (result.not_found.length > 0) {
      message.warning(`${result.not_found.length} records were already missing`)
    }
    setSelectedRowKeys([])
    await load()
  }

  const columns: TableColumnsType<TaskLogItem> = [
    {
      title: 'Time',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (text: string) => (text ? new Date(text).toLocaleString('en-US') : '-'),
    },
    {
      title: 'Platform',
      dataIndex: 'platform',
      key: 'platform',
      width: 100,
      render: (text: string) => <Tag>{text}</Tag>,
    },
    {
      title: 'Email',
      dataIndex: 'email',
      key: 'email',
      render: (text: string) => <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{text}</span>,
    },
    {
      title: 'Status',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string) => (
        <Tag color={status === 'success' ? 'success' : 'error'}>
          {status === 'success' ? 'Success' : 'Failed'}
        </Tag>
      ),
    },
    {
      title: 'Error',
      dataIndex: 'error',
      key: 'error',
      render: (text: string) => text || '-',
    },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 'bold', margin: 0 }}>Task History</h1>
          <p style={{ color: '#7a8ba3', marginTop: 4 }}>Execution log for registration tasks</p>
        </div>
        <Space>
          <Text type="secondary">{total} records</Text>
          {selectedRowKeys.length > 0 && <Text type="success">{selectedRowKeys.length} selected</Text>}
          {selectedRowKeys.length > 0 && (
            <Popconfirm
              title={`Delete ${selectedRowKeys.length} selected task history entries?`}
              onConfirm={handleBatchDelete}
            >
              <Button danger icon={<DeleteOutlined />}>
                Delete {selectedRowKeys.length}
              </Button>
            </Popconfirm>
          )}
          <Select
            value={platform}
            onChange={(value) => {
              setPlatform(value)
              setSelectedRowKeys([])
            }}
            style={{ width: 140 }}
            options={[
              { value: '', label: 'All Platforms' },
              { value: 'trae', label: 'Trae' },
              { value: 'cursor', label: 'Cursor' },
            ]}
          />
          <Button icon={<ReloadOutlined spin={loading} />} onClick={load} loading={loading} />
        </Space>
      </div>

      <Card>
        <Table
          rowKey="id"
          columns={columns}
          dataSource={logs}
          loading={loading}
          rowSelection={{
            selectedRowKeys,
            onChange: (keys) => setSelectedRowKeys(keys as number[]),
          }}
          pagination={{ pageSize: 20, showSizeChanger: false }}
        />
      </Card>
    </div>
  )
}
