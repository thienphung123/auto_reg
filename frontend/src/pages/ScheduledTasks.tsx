import { useEffect, useState } from 'react'
import { Card, Table, Button, Tag, Modal, Form, Input, InputNumber, Select, message, Alert, Radio, Space } from 'antd'
import { PlusOutlined, DeleteOutlined, EditOutlined, PlayCircleOutlined, PauseCircleOutlined, SyncOutlined } from '@ant-design/icons'
import { apiFetch } from '@/lib/utils'

export default function ScheduledTasks() {
  const [tasks, setTasks] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editingTask, setEditingTask] = useState<any>(null)
  const [runningTasks, setRunningTasks] = useState<Record<string, string>>({})
  const [form] = Form.useForm()

  const loadTasks = async () => {
    setLoading(true)
    try {
      const [taskData, workerData] = await Promise.all([
        apiFetch('/tasks/schedule'),
        apiFetch('/tasks/workers').catch(() => ({ running_scheduled: {} })),
      ])
      setTasks(taskData.tasks || [])
      setRunningTasks(workerData.running_scheduled || {})
    } catch (e: any) {
      message.error(`Failed to load tasks: ${e.message}`)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadTasks()
    const timer = setInterval(loadTasks, 10000)
    return () => clearInterval(timer)
  }, [])

  const handleCreate = async () => {
    try {
      const values = await form.validateFields()
      const payload: any = {
        platform: values.platform,
        count: values.count,
        executor_type: values.executor_type,
        captcha_solver: values.captcha_solver,
        extra: {
          mail_provider: values.mail_provider,
          network_mode: values.network_mode,
          fotor_ref_link: values.fotor_ref_link,
          fotor_ref_limit: values.fotor_ref_limit,
        },
        interval_type: values.interval_type,
        interval_value: values.interval_value,
      }

      if (editingTask) {
        payload.task_id = editingTask.task_id
        await apiFetch('/tasks/schedule', {
          method: 'PUT',
          body: JSON.stringify(payload),
        })
        message.success('Task updated')
      } else {
        await apiFetch('/tasks/schedule', {
          method: 'POST',
          body: JSON.stringify(payload),
        })
        message.success('Task created')
      }
      setModalOpen(false)
      setEditingTask(null)
      form.resetFields()
      loadTasks()
    } catch (e: any) {
      message.error(`Operation failed: ${e.message}`)
    }
  }

  const handleEdit = (task: any) => {
    setEditingTask(task)
    form.setFieldsValue({
      platform: task.platform,
      count: task.count,
      executor_type: task.executor_type,
      captcha_solver: task.captcha_solver,
      mail_provider: task.extra?.mail_provider,
      network_mode: task.extra?.network_mode || 'proxy',
      fotor_ref_link: task.extra?.fotor_ref_link,
      fotor_ref_limit: task.extra?.fotor_ref_limit,
      interval_value: task.interval_value,
      interval_type: task.interval_type || 'minutes',
    })
    setModalOpen(true)
  }

  const handleDelete = async (taskId: string) => {
    try {
      await apiFetch(`/tasks/schedule/${taskId}`, { method: 'DELETE' })
      message.success('Task deleted')
      loadTasks()
    } catch (e: any) {
      message.error(`Delete failed: ${e.message}`)
    }
  }

  const handleRun = async (task: any) => {
    try {
      await apiFetch(`/tasks/schedule/${task.task_id}/run`, { method: 'POST' })
      message.success('Task started')
      loadTasks()
    } catch (e: any) {
      message.error(`Start failed: ${e.message}`)
    }
  }

  const handlePause = async (task: any) => {
    try {
      await apiFetch(`/tasks/schedule/${task.task_id}/toggle`, { method: 'POST' })
      message.success('Task status updated')
      loadTasks()
    } catch (e: any) {
      message.error(`Operation failed: ${e.message}`)
    }
  }

  const columns = [
    {
      title: 'Task ID',
      dataIndex: 'task_id',
      key: 'task_id',
      width: 120,
    },
    {
      title: 'Platform',
      dataIndex: 'platform',
      key: 'platform',
      width: 100,
      render: (text: string) => <Tag>{text}</Tag>,
    },
    {
      title: 'Count',
      dataIndex: 'count',
      key: 'count',
      width: 70,
    },
    {
      title: 'Network',
      key: 'network_mode',
      width: 120,
      render: (_: any, record: any) => {
        const mode = record.extra?.network_mode === 'direct' ? 'direct' : 'proxy'
        return <Tag color={mode === 'direct' ? 'default' : 'blue'}>{mode === 'direct' ? 'Direct' : 'Proxy'}</Tag>
      },
    },
    {
      title: 'Interval',
      key: 'interval',
      width: 120,
      render: (_: any, record: any) => {
        const type = record.interval_type === 'minutes' ? 'minutes' : 'hours'
        const value = record.interval_value || 0
        return <Tag color="blue">Every {value} {type}</Tag>
      },
    },
    {
      title: 'Status',
      key: 'status',
      width: 130,
      render: (_: any, record: any) => {
        if (record.task_id in runningTasks) {
          return <Tag icon={<SyncOutlined spin />} color="processing">Running</Tag>
        }
        if (record.paused) return <Tag color="warning">Paused</Tag>
        if (!record.last_run_at) return <Tag>Pending</Tag>
        return record.last_run_success ? (
          <Tag color="success">Success</Tag>
        ) : (
          <Tag color="error">Failed</Tag>
        )
      },
    },
    {
      title: 'Last Run',
      key: 'last_run',
      width: 180,
      render: (_: any, record: any) => {
        if (!record.last_run_at) return '-'
        const date = new Date(record.last_run_at)
        return date.toLocaleString('en-US')
      },
    },
    {
      title: 'Error',
      dataIndex: 'last_error',
      key: 'error',
      ellipsis: true,
    },
    {
      title: 'Actions',
      key: 'action',
      width: 220,
      render: (_: any, record: any) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            icon={<PlayCircleOutlined />}
            onClick={() => handleRun(record)}
          >
            Run
          </Button>
          <Button
            type="link"
            size="small"
            icon={<EditOutlined />}
            onClick={() => handleEdit(record)}
          >
            Edit
          </Button>
          <Button
            type="link"
            size="small"
            icon={record.paused ? <PlayCircleOutlined /> : <PauseCircleOutlined />}
            onClick={() => handlePause(record)}
          >
            {record.paused ? 'Resume' : 'Pause'}
          </Button>
          <Button
            type="link"
            size="small"
            danger
            icon={<DeleteOutlined />}
            onClick={() => handleDelete(record.task_id)}
          >
            Delete
          </Button>
        </Space>
      ),
    },
  ]

  return (
    <div style={{ padding: 24 }}>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 24 }}>Scheduled Tasks</h1>
          <p style={{ color: '#999', marginTop: 8 }}>Run registration tasks automatically</p>
        </div>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => {
            setEditingTask(null)
            form.resetFields()
            setModalOpen(true)
          }}
        >
          Create Task
        </Button>
      </div>

      <Card>
        <Alert
          message="The scheduler checks due tasks every minute and runs them automatically."
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
        />
        <Table
          columns={columns}
          dataSource={tasks}
          rowKey="task_id"
          loading={loading}
          pagination={false}
        />
      </Card>

      <Modal
        title={editingTask ? 'Edit Task' : 'Create Task'}
        open={modalOpen}
        onOk={handleCreate}
        onCancel={() => {
          setModalOpen(false)
          setEditingTask(null)
          form.resetFields()
        }}
        width={500}
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{
            platform: 'chatgpt',
            count: 10,
            executor_type: 'protocol',
            captcha_solver: 'yescaptcha',
            mail_provider: 'duckmail',
            network_mode: 'proxy',
            fotor_ref_link: 'https://www.fotor.com/referrer/ce1yh8e7',
            fotor_ref_limit: '20',
            interval_value: 30,
            interval_type: 'minutes',
          }}
        >
          <Form.Item
            name="platform"
            label="Platform"
            rules={[{ required: true }]}
          >
            <Select
              options={[
                { value: 'chatgpt', label: 'ChatGPT' },
                { value: 'trae', label: 'Trae' },
                { value: 'cursor', label: 'Cursor' },
                { value: 'fotor', label: 'Fotor' },
              ]}
            />
          </Form.Item>

          <Form.Item
            name="count"
            label="Count per Run"
            rules={[{ required: true }]}
          >
            <InputNumber min={1} max={1000} style={{ width: '100%' }} />
          </Form.Item>

          <Form.Item
            name="interval_value"
            label="Interval"
            rules={[{ required: true }]}
          >
            <InputNumber min={1} style={{ width: '100%' }} />
          </Form.Item>

          <Form.Item
            name="interval_type"
            label="Time Unit"
            rules={[{ required: true }]}
          >
            <Radio.Group>
              <Radio value="minutes">Minutes</Radio>
              <Radio value="hours">Hours</Radio>
            </Radio.Group>
          </Form.Item>

          <Form.Item name="executor_type" label="Executor">
            <Select
              options={[
                { value: 'protocol', label: 'Protocol Mode' },
                { value: 'headless', label: 'Headless Browser' },
              ]}
            />
          </Form.Item>

          <Form.Item name="captcha_solver" label="Captcha Solver">
            <Select
              options={[
                { value: 'yescaptcha', label: 'YesCaptcha' },
                { value: 'local_solver', label: 'Local Solver' },
              ]}
            />
          </Form.Item>

          <Form.Item name="mail_provider" label="Mailbox Provider">
            <Select
              options={[
                { value: 'tempmail_lol', label: 'TempMail' },
                { value: 'tempmail', label: 'TempMailo (UI 2-Tab Scraping)' },
                { value: 'mail.tm', label: 'Mail.tm (API Gốc)' },
                { value: 'moemail', label: 'MoeMail (sall.cc)' },
                { value: 'freemail', label: 'Freemail (self-hosted)' },
                { value: 'luckmail', label: 'LuckMail' },
                { value: 'skymail', label: 'SkyMail (CloudMail)' },
                { value: 'duckmail', label: 'DuckMail' },
                { value: 'laoudo', label: 'Laoudo' },
              ]}
            />
          </Form.Item>

          <Form.Item name="network_mode" label="Network Mode">
            <Select
              options={[
                { value: 'proxy', label: 'Proxy' },
                { value: 'direct', label: 'Direct' },
              ]}
            />
          </Form.Item>

          <Form.Item noStyle shouldUpdate={(prev, curr) => prev.platform !== curr.platform}>
            {({ getFieldValue }) =>
              getFieldValue('platform') === 'fotor' ? (
                <>
                  <Form.Item name="fotor_ref_link" label="Fotor Referral Link">
                    <Input placeholder="https://www.fotor.com/referrer/ce1yh8e7" />
                  </Form.Item>
                  <Form.Item name="fotor_ref_limit" label="Suggested Referral Cap">
                    <Input placeholder="20" />
                  </Form.Item>
                </>
              ) : null
            }
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
