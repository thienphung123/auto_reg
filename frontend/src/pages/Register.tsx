import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

export default function Register() {
  const navigate = useNavigate()

  useEffect(() => {
    navigate('/register-task')
  }, [navigate])

  return (
    <div style={{ padding: 24, textAlign: 'center' }}>
      <p>Redirecting to the registration task page...</p>
    </div>
  )
}
