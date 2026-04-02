import { useEffect, useRef, useState } from 'react';
import { Badge, Button, Form, Input, Modal, Typography } from 'antd';
import { MessageOutlined, SendOutlined, CloseOutlined } from '@ant-design/icons';
import { useSettings } from '../context/SettingsContext';
import { useTranslation } from '../i18n';

interface ChatMessage {
  id: number;
  from: string;
  content: string;
  created_at: string;
}

type ChatState = 'idle' | 'joining' | 'connected' | 'closed';

export default function ChatWidget() {
  const { settings } = useSettings();
  const { t } = useTranslation();
  const adminUrl = settings.admin_server_url;

  const [open, setOpen] = useState(false);
  const [chatState, setChatState] = useState<ChatState>('idle');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputVal, setInputVal] = useState('');
  const [unreadCount, setUnreadCount] = useState(0);
  const [adminTyping, setAdminTyping] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const typingTimeout = useRef<ReturnType<typeof setTimeout>>();
  const [form] = Form.useForm();

  const isDark = settings.theme === 'dark';

  // 메시지 스크롤
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // 창이 열릴 때 unread 초기화
  useEffect(() => {
    if (open) setUnreadCount(0);
  }, [open]);

  const handleJoin = async () => {
    const values = await form.validateFields();
    setChatState('joining');

    const wsUrl = adminUrl.replace(/^http/, 'ws') + '/ws/chat';
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      ws.send(JSON.stringify({
        type: 'join',
        name: values.name,
        department: values.department,
      }));
    };

    ws.onmessage = (ev) => {
      const data = JSON.parse(ev.data);
      switch (data.type) {
        case 'joined':
          setChatState('connected');
          break;
        case 'message':
          setMessages(prev => [...prev, {
            id: Date.now(),
            from: data.from,
            content: data.content,
            created_at: data.created_at || new Date().toISOString(),
          }]);
          if (!open) setUnreadCount(c => c + 1);
          break;
        case 'admin_typing':
          setAdminTyping(true);
          clearTimeout(typingTimeout.current);
          typingTimeout.current = setTimeout(() => setAdminTyping(false), 2000);
          break;
        case 'closed':
          setChatState('closed');
          setMessages(prev => [...prev, {
            id: Date.now(),
            from: 'system',
            content: data.message || '채팅이 종료되었습니다.',
            created_at: new Date().toISOString(),
          }]);
          break;
        case 'error':
          setChatState('idle');
          break;
      }
    };

    ws.onclose = () => {
      if (chatState !== 'closed') {
        setChatState('closed');
      }
    };
  };

  const handleSend = () => {
    if (!inputVal.trim() || !wsRef.current) return;
    wsRef.current.send(JSON.stringify({ type: 'message', content: inputVal.trim() }));
    setMessages(prev => [...prev, {
      id: Date.now(),
      from: 'me',
      content: inputVal.trim(),
      created_at: new Date().toISOString(),
    }]);
    setInputVal('');
  };

  const handleClose = () => {
    setOpen(false);
  };

  const handleReset = () => {
    wsRef.current?.close();
    wsRef.current = null;
    setChatState('idle');
    setMessages([]);
    setInputVal('');
    form.resetFields();
  };

  // cleanup on unmount
  useEffect(() => {
    return () => {
      wsRef.current?.close();
    };
  }, []);

  if (!adminUrl) return null;

  return (
    <>
      {/* 플로팅 버튼 */}
      <div style={{
        position: 'fixed',
        bottom: 24,
        right: 24,
        zIndex: 1000,
      }}>
        <Badge count={unreadCount} offset={[-6, 6]}>
          <Button
            type="primary"
            shape="circle"
            size="large"
            icon={<MessageOutlined />}
            onClick={() => setOpen(true)}
            style={{
              width: 56,
              height: 56,
              fontSize: 24,
              boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
            }}
          />
        </Badge>
      </div>

      {/* 채팅 모달 */}
      <Modal
        title={null}
        open={open}
        onCancel={handleClose}
        footer={null}
        width={420}
        style={{ top: 'auto', bottom: 80, right: 24, position: 'fixed', margin: 0 }}
        styles={{ body: { padding: 0, height: 500, display: 'flex', flexDirection: 'column' } }}
        mask={false}
        closable={false}
      >
        {/* 헤더 */}
        <div style={{
          padding: '12px 16px',
          background: '#1677ff',
          color: '#fff',
          borderRadius: '8px 8px 0 0',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}>
          <Typography.Text strong style={{ color: '#fff', fontSize: 15 }}>
            {t('chat.title')}
          </Typography.Text>
          <Button type="text" size="small" icon={<CloseOutlined />} onClick={handleClose} style={{ color: '#fff' }} />
        </div>

        {chatState === 'idle' || chatState === 'joining' ? (
          /* 이름/부서 입력 폼 */
          <div style={{ padding: 24, flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center' }}>
            <Typography.Title level={5} style={{ textAlign: 'center', marginBottom: 24 }}>
              {t('chat.enterInfo')}
            </Typography.Title>
            <Form form={form} layout="vertical">
              <Form.Item name="name" label={t('chat.name')} rules={[{ required: true, message: t('chat.nameRequired') }]}>
                <Input placeholder={t('chat.namePlaceholder')} autoFocus />
              </Form.Item>
              <Form.Item name="department" label={t('chat.department')} rules={[{ required: true, message: t('chat.deptRequired') }]}>
                <Input placeholder={t('chat.deptPlaceholder')} />
              </Form.Item>
              <Button type="primary" block size="large" loading={chatState === 'joining'} onClick={handleJoin}>
                {t('chat.startChat')}
              </Button>
            </Form>
          </div>
        ) : (
          /* 채팅 영역 */
          <>
            <div style={{ flex: 1, overflow: 'auto', padding: 12 }}>
              {messages.map(msg => (
                <div
                  key={msg.id}
                  style={{
                    display: 'flex',
                    justifyContent: msg.from === 'me' ? 'flex-end' : msg.from === 'system' ? 'center' : 'flex-start',
                    marginBottom: 8,
                  }}
                >
                  {msg.from === 'system' ? (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>{msg.content}</Typography.Text>
                  ) : (
                    <div style={{
                      maxWidth: '75%',
                      padding: '8px 12px',
                      borderRadius: 12,
                      background: msg.from === 'me'
                        ? '#1677ff'
                        : isDark ? '#303030' : '#e8e8e8',
                      color: msg.from === 'me' ? '#fff' : undefined,
                    }}>
                      {msg.from === 'admin' && (
                        <div style={{ fontSize: 11, color: isDark ? '#888' : '#666', marginBottom: 2 }}>{t('chat.admin')}</div>
                      )}
                      <div style={{ fontSize: 13 }}>{msg.content}</div>
                      <div style={{
                        fontSize: 10,
                        color: msg.from === 'me' ? 'rgba(255,255,255,0.5)' : isDark ? '#666' : '#999',
                        marginTop: 2,
                        textAlign: 'right',
                      }}>
                        {new Date(msg.created_at).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' })}
                      </div>
                    </div>
                  )}
                </div>
              ))}
              {adminTyping && (
                <div style={{ fontSize: 12, color: '#888', padding: '4px 0' }}>{t('chat.adminTyping')}</div>
              )}
              <div ref={messagesEndRef} />
            </div>

            {/* 입력 영역 */}
            {chatState === 'connected' ? (
              <div style={{ padding: '8px 12px', borderTop: `1px solid ${isDark ? '#303030' : '#d0d0d0'}`, display: 'flex', gap: 8 }}>
                <Input
                  value={inputVal}
                  onChange={e => {
                    setInputVal(e.target.value);
                    wsRef.current?.send(JSON.stringify({ type: 'typing' }));
                  }}
                  onPressEnter={handleSend}
                  placeholder={t('chat.inputPlaceholder')}
                  autoFocus
                />
                <Button type="primary" icon={<SendOutlined />} onClick={handleSend} disabled={!inputVal.trim()} />
              </div>
            ) : (
              <div style={{ padding: '12px', textAlign: 'center', borderTop: `1px solid ${isDark ? '#303030' : '#d0d0d0'}` }}>
                <Button onClick={handleReset}>{t('chat.newChat')}</Button>
              </div>
            )}
          </>
        )}
      </Modal>
    </>
  );
}
