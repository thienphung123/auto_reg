import { useEffect, useState } from 'react'
import { Card, Table, Button, Tag, Modal, Form, InputNumber, Select, message, Alert, Radio, Space } from 'antd'
import { PlusOutlined, DeleteOutlined, EditOutlined, PlayCircleOutlined, PauseCircleOutlined } from '@ant-design/icons'
import { apiFetch } from '@/lib/utils'

export default function ScheduledTasks() {
  const [tasks, setTasks] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editingTask, setEditingTask] = useState<any>(null)
  const [form] = Form.useForm()

  const loadTasks = async () => {
    setLoading(true)
    try {
      const data = await apiFetch('/tasks/schedule')
      setTasks(data.tasks || [])
    } catch (e: any) {
      message.error('加载失败：' + e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadTasks()
    const timer = setInterval(loadTasks, 30000)
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
        extra: { mail_provider: values.mail_provider },
        interval_type: values.interval_type,
        interval_value: values.interval_value,
      }

      if (editingTask) {
        payload.task_id = editingTask.task_id
        await apiFetch('/tasks/schedule', {
          method: 'PUT',
          body: JSON.stringify(payload),
        })
        message.success('任务更新成功')
      } else {
        await apiFetch('/tasks/schedule', {
          method: 'POST',
          body: JSON.stringify(payload),
        })
        message.success('任务创建成功')
      }
      setModalOpen(false)
      setEditingTask(null)
      form.resetFields()
      loadTasks()
    } catch (e: any) {
      message.error('操作失败：' + e.message)
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
      interval_value: task.interval_value,
      interval_type: task.interval_type || 'minutes',
    })
    setModalOpen(true)
  }

  const handleDelete = async (taskId: string) => {
    try {
      await apiFetch(`/tasks/schedule/${taskId}`, { method: 'DELETE' })
      message.success('删除成功')
      loadTasks()
    } catch (e: any) {
      message.error('删除失败：' + e.message)
    }
  }

  const handleRun = async (task: any) => {
    try {
      await apiFetch(`/tasks/schedule/${task.task_id}/run`, { method: 'POST' })
      message.success('任务已启动')
      loadTasks()
    } catch (e: any) {
      message.error('启动失败：' + e.message)
    }
  }

  const handlePause = async (task: any) => {
    try {
      await apiFetch(`/tasks/schedule/${task.task_id}/toggle`, { method: 'POST' })
      message.success('状态已更新')
      loadTasks()
    } catch (e: any) {
      message.error('操作失败：' + e.message)
    }
  }

  const columns = [
    {
      title: '任务 ID',
      dataIndex: 'task_id',
      key: 'task_id',
      width: 120,
    },
    {
      title: '平台',
      dataIndex: 'platform',
      key: 'platform',
      width: 100,
      render: (text: string) => <Tag>{text}</Tag>,
    },
    {
      title: '数量',
      dataIndex: 'count',
      key: 'count',
      width: 60,
    },
    {
      title: '间隔',
      key: 'interval',
      width: 120,
      render: (_: any, record: any) => {
        const type = record.interval_type === 'minutes' ? '分钟' : '小时'
        const value = record.interval_value || 0
        return <Tag color="blue">每 {value} {type}</Tag>
      },
    },
    {
      title: '状态',
      key: 'status',
      width: 100,
      render: (_: any, record: any) => {
        if (record.paused) return <Tag color="warning">已暂停</Tag>
        if (!record.last_run_at) return <Tag>等待中</Tag>
        return record.last_run_success ? (
          <Tag color="success">成功</Tag>
        ) : (
          <Tag color="error">失败</Tag>
        )
      },
    },
    {
      title: '上次运行',
      key: 'last_run',
      width: 180,
      render: (_: any, record: any) => {
        if (!record.last_run_at) return '-'
        const date = new Date(record.last_run_at)
        return date.toLocaleString('zh-CN')
      },
    },
    {
      title: '错误',
      dataIndex: 'last_error',
      key: 'error',
      ellipsis: true,
    },
    {
      title: '操作',
      key: 'action',
      width: 200,
      render: (_: any, record: any) => (
        <Space size="small">
          <Button
            type="link"
            size="small"
            icon={<PlayCircleOutlined />}
            onClick={() => handleRun(record)}
          >
            运行
          </Button>
          <Button
            type="link"
            size="small"
            icon={<EditOutlined />}
            onClick={() => handleEdit(record)}
          >
            编辑
          </Button>
          <Button
            type="link"
            size="small"
            icon={record.paused ? <PlayCircleOutlined /> : <PauseCircleOutlined />}
            onClick={() => handlePause(record)}
          >
            {record.paused ? '恢复' : '暂停'}
          </Button>
          <Button
            type="link"
            size="small"
            danger
            icon={<DeleteOutlined />}
            onClick={() => handleDelete(record.task_id)}
          >
            删除
          </Button>
        </Space>
      ),
    },
  ]

  return (
    <div style={{ padding: 24 }}>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 24 }}>定时任务</h1>
          <p style={{ color: '#999', marginTop: 8 }}>自动执行注册任务</p>
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
          创建任务
        </Button>
      </div>

      <Card>
        <Alert
          message="系统每分钟检查一次定时任务，到达设定时间后自动执行"
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
        title={editingTask ? '编辑任务' : '创建任务'}
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
            mail_provider: 'tempmail_lol',
            interval_value: 30,
            interval_type: 'minutes',
          }}
        >
          <Form.Item
            name="platform"
            label="平台"
            rules={[{ required: true }]}
          >
            <Select
              options={[
                { value: 'chatgpt', label: 'ChatGPT' },
                { value: 'trae', label: 'Trae' },
                { value: 'cursor', label: 'Cursor' },
              ]}
            />
          </Form.Item>

          <Form.Item
            name="count"
            label="每次数量"
            rules={[{ required: true }]}
          >
            <InputNumber min={1} max={1000} style={{ width: '100%' }} />
          </Form.Item>

          <Form.Item
            name="interval_value"
            label="间隔时间"
            rules={[{ required: true }]}
          >
            <InputNumber min={1} style={{ width: '100%' }} />
          </Form.Item>

          <Form.Item
            name="interval_type"
            label="时间单位"
            rules={[{ required: true }]}
          >
            <Radio.Group>
              <Radio value="minutes">分钟</Radio>
              <Radio value="hours">小时</Radio>
            </Radio.Group>
          </Form.Item>

          <Form.Item name="executor_type" label="执行器">
            <Select
              options={[
                { value: 'protocol', label: '协议模式' },
                { value: 'headless', label: '无头浏览器' },
              ]}
            />
          </Form.Item>

          <Form.Item name="captcha_solver" label="验证码">
            <Select
              options={[
                { value: 'yescaptcha', label: 'YesCaptcha' },
                { value: 'local_solver', label: '本地 Solver' },
              ]}
            />
          </Form.Item>

          <Form.Item name="mail_provider" label="邮箱服务">
            <Select
              options={[
                { value: 'tempmail_lol', label: 'TempMail' },
                { value: 'moemail', label: 'MoeMail' },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
