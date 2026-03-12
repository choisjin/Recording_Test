import { useState } from 'react';
import { ConfigProvider, Layout, Menu, theme } from 'antd';
import {
  DesktopOutlined,
  PlayCircleOutlined,
  VideoCameraOutlined,
} from '@ant-design/icons';
import { DeviceProvider } from './context/DeviceContext';
import DevicePage from './pages/DevicePage';
import RecordPage from './pages/RecordPage';
import ScenarioPage from './pages/ScenarioPage';

const { Sider, Content } = Layout;

const pages = [
  { key: '/', icon: <DesktopOutlined />, label: '디바이스', component: <DevicePage /> },
  { key: '/record', icon: <VideoCameraOutlined />, label: '녹화', component: <RecordPage /> },
  { key: '/scenarios', icon: <PlayCircleOutlined />, label: '시나리오', component: <ScenarioPage /> },
];

function App() {
  const [activeKey, setActiveKey] = useState('/');

  const menuItems = pages.map(({ key, icon, label }) => ({ key, icon, label }));

  return (
    <ConfigProvider theme={{ algorithm: theme.darkAlgorithm }}>
      <DeviceProvider>
      <Layout style={{ minHeight: '100vh' }}>
        <Sider collapsible>
          <div style={{ height: 40, margin: 16, color: '#fff', fontSize: 14, fontWeight: 'bold', textAlign: 'center', lineHeight: '40px' }}>
            Menu
          </div>
          <Menu
            theme="dark"
            mode="inline"
            selectedKeys={[activeKey]}
            items={menuItems}
            onClick={({ key }) => setActiveKey(key)}
          />
        </Sider>
        <Layout>
          <Content style={{ margin: 8, padding: 12, background: '#1f1f1f', borderRadius: 8 }}>
            {pages.map(({ key, component }) => (
              <div key={key} style={{ display: activeKey === key ? 'block' : 'none' }}>
                {component}
              </div>
            ))}
          </Content>
        </Layout>
      </Layout>
      </DeviceProvider>
    </ConfigProvider>
  );
}

export default App;
