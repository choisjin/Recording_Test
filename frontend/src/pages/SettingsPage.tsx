import { useEffect, useState } from 'react';
import { Alert, Badge, Button, Card, Input, InputNumber, Modal, Select, Space, Switch, Tag, message, Typography } from 'antd';
import { ApiOutlined, FolderOpenOutlined, SwapOutlined } from '@ant-design/icons';
import { useSettings } from '../context/SettingsContext';
import { useTranslation } from '../i18n';
import { deviceApi } from '../services/api';

const { Text } = Typography;

type MigrationReport = {
  auxiliary_devices: { changed: number; saved: boolean };
  scan_settings: { changed: number; saved: boolean };
  device_catalog: { changed: number; saved: boolean };
  scenarios: Array<{
    file: string;
    changed: number;
    saved: boolean;
    unmapped_functions: Array<{ step_id: number | null; module: string; function: string }>;
  }>;
  summary: {
    files_changed: number;
    total_changes: number;
    scenarios_with_unmapped: number;
    dry_run: boolean;
    module_renames: Record<string, string>;
    function_renames: Record<string, Record<string, string>>;
  };
};

export default function SettingsPage() {
  const { settings, updateSettings, browseFolder } = useSettings();
  const { t } = useTranslation();
  const [excelDir, setExcelDir] = useState(settings.excel_export_dir);
  const [exportDir, setExportDir] = useState(settings.scenario_export_dir);
  const [monitorUrl, setMonitorUrl] = useState(settings.monitor_server_url);

  // Sync local state when settings load
  useEffect(() => {
    setExcelDir(settings.excel_export_dir);
    setExportDir(settings.scenario_export_dir);
    setMonitorUrl(settings.monitor_server_url);
  }, [settings.excel_export_dir, settings.scenario_export_dir, settings.monitor_server_url]);

  const handleThemeToggle = async (checked: boolean) => {
    try {
      await updateSettings({ theme: checked ? 'dark' : 'light' });
    } catch {
      message.error(t('settings.themeChanged'));
    }
  };

  const handleExcelDirSave = async () => {
    try {
      await updateSettings({ excel_export_dir: excelDir.trim() });
      message.success(t('settings.excelDirSuccess'));
    } catch {
      message.error(t('common.saveFailed'));
    }
  };

  const handleExportDirSave = async () => {
    try {
      await updateSettings({ scenario_export_dir: exportDir.trim() });
      message.success(t('settings.exportDirSuccess'));
    } catch {
      message.error(t('common.saveFailed'));
    }
  };

  const handleLanguageChange = async (lang: 'ko' | 'en') => {
    try {
      await updateSettings({ language: lang });
    } catch {
      message.error(t('common.saveFailed'));
    }
  };

  // ── 플러그인 마이그레이션 ─────────────────────────────────────────
  const [migrationLoading, setMigrationLoading] = useState(false);
  const [migrationReport, setMigrationReport] = useState<MigrationReport | null>(null);
  const [migrationModalOpen, setMigrationModalOpen] = useState(false);

  const handleMigrationPreview = async () => {
    setMigrationLoading(true);
    try {
      const res = await deviceApi.previewPluginMigration();
      setMigrationReport(res.data as MigrationReport);
      setMigrationModalOpen(true);
    } catch (e: any) {
      message.error(`${t('settings.pluginMigrationFailed')}: ${e?.message || e}`);
    } finally {
      setMigrationLoading(false);
    }
  };

  const handleMigrationApply = async () => {
    setMigrationLoading(true);
    try {
      const res = await deviceApi.applyPluginMigration();
      const report = res.data as MigrationReport;
      setMigrationReport(report);
      setMigrationModalOpen(true);
      if (report.summary.total_changes === 0) {
        message.info(t('settings.pluginMigrationNoChange'));
      } else {
        message.success(`${t('settings.pluginMigrationApplied')} — ${report.summary.total_changes} changes in ${report.summary.files_changed} file(s)`);
      }
    } catch (e: any) {
      message.error(`${t('settings.pluginMigrationFailed')}: ${e?.message || e}`);
    } finally {
      setMigrationLoading(false);
    }
  };

  return (
    <div>
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <Card title={t('settings.language')} size="small">
          <Space>
            <Select
              value={settings.language || 'ko'}
              onChange={handleLanguageChange}
              style={{ width: 200 }}
              options={[
                { label: '한국어 (Korean)', value: 'ko' },
                { label: 'English', value: 'en' },
              ]}
            />
          </Space>
          <Text type="secondary" style={{ fontSize: 11, marginTop: 3, display: 'block' }}>
            {t('settings.languageDesc')}
          </Text>
        </Card>

        <Card title={t('settings.theme')} size="small">
          <Space>
            <Text>Light</Text>
            <Switch
              checked={settings.theme === 'dark'}
              onChange={handleThemeToggle}
              checkedChildren="Dark"
              unCheckedChildren="Light"
            />
            <Text>Dark</Text>
          </Space>
        </Card>

        <Card title={t('settings.excelDir')} size="small">
          <Space.Compact style={{ width: '100%' }}>
            <Input
              placeholder={t('settings.excelDirPlaceholder')}
              value={excelDir}
              onChange={(e) => setExcelDir(e.target.value)}
              onPressEnter={handleExcelDirSave}
              style={{ flex: 1 }}
            />
            <Button
              icon={<FolderOpenOutlined />}
              onClick={async () => {
                try {
                  const path = await browseFolder(excelDir);
                  if (path) { setExcelDir(path); await updateSettings({ excel_export_dir: path }); message.success(t('settings.excelDirSuccess')); }
                } catch { message.error(t('settings.folderSelectFailed')); }
              }}
            />
            <Button type="primary" onClick={handleExcelDirSave}>{t('common.save')}</Button>
          </Space.Compact>
          <Text type="secondary" style={{ fontSize: 11, marginTop: 3, display: 'block' }}>
            {t('settings.excelDirDesc')}
          </Text>
        </Card>

        <Card title={t('settings.exportDir')} size="small">
          <Space.Compact style={{ width: '100%' }}>
            <Input
              placeholder={t('settings.exportDirPlaceholder')}
              value={exportDir}
              onChange={(e) => setExportDir(e.target.value)}
              onPressEnter={handleExportDirSave}
              style={{ flex: 1 }}
            />
            <Button
              icon={<FolderOpenOutlined />}
              onClick={async () => {
                try {
                  const path = await browseFolder(exportDir);
                  if (path) { setExportDir(path); await updateSettings({ scenario_export_dir: path }); message.success(t('settings.exportDirSuccess')); }
                } catch { message.error(t('settings.folderSelectFailed')); }
              }}
            />
            <Button type="primary" onClick={handleExportDirSave}>{t('common.save')}</Button>
          </Space.Compact>
          <Text type="secondary" style={{ fontSize: 11, marginTop: 3, display: 'block' }}>
            {t('settings.exportDirDesc')}
          </Text>
        </Card>

        <Card
          title={
            <Space>
              <ApiOutlined />
              {t('settings.monitorServer')}
              {monitorUrl ? <Badge status="processing" text="" /> : null}
            </Space>
          }
          size="small"
        >
          <Space.Compact style={{ width: '100%' }}>
            <Input
              placeholder={t('settings.monitorServerPlaceholder')}
              value={monitorUrl}
              onChange={(e) => setMonitorUrl(e.target.value)}
              onPressEnter={async () => {
                try {
                  await updateSettings({ monitor_server_url: monitorUrl.trim() });
                  message.success(t('settings.monitorServerSuccess'));
                } catch { message.error(t('common.saveFailed')); }
              }}
              style={{ flex: 1 }}
            />
            <Button
              type="primary"
              onClick={async () => {
                try {
                  await updateSettings({ monitor_server_url: monitorUrl.trim() });
                  message.success(t('settings.monitorServerSuccess'));
                } catch { message.error(t('common.saveFailed')); }
              }}
            >
              {t('common.save')}
            </Button>
          </Space.Compact>
          <Text type="secondary" style={{ fontSize: 11, marginTop: 3, display: 'block' }}>
            {t('settings.monitorServerDesc')}
          </Text>
        </Card>

        <Card
          title={
            <Space>
              <SwapOutlined />
              {t('settings.pluginMigration')}
            </Space>
          }
          size="small"
        >
          <Text type="secondary" style={{ fontSize: 11, marginBottom: 8, display: 'block' }}>
            {t('settings.pluginMigrationDesc')}
          </Text>
          <Space>
            <Button onClick={handleMigrationPreview} loading={migrationLoading}>
              {t('settings.pluginMigrationPreview')}
            </Button>
            <Button type="primary" onClick={handleMigrationApply} loading={migrationLoading}>
              {t('settings.pluginMigrationApply')}
            </Button>
          </Space>
        </Card>

        <Card title={t('settings.thresholdTitle')} size="small">
          <Text type="secondary" style={{ fontSize: 11, marginBottom: 10, display: 'block' }}>
            {t('settings.thresholdDesc')}
          </Text>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {([
              { key: 'threshold_full', label: t('settings.thresholdFull') },
              { key: 'threshold_single_crop', label: t('settings.thresholdCrop') },
              { key: 'threshold_full_exclude', label: t('settings.thresholdExclude') },
              { key: 'threshold_multi_crop', label: t('settings.thresholdMulti') },
            ] as const).map(({ key, label }) => (
              <Space key={key}>
                <span style={{ minWidth: 100, display: 'inline-block' }}>{label}</span>
                <InputNumber
                  size="small"
                  min={0} max={1} step={0.01}
                  value={settings[key]}
                  onChange={(v) => updateSettings({ [key]: v ?? 0.95 })}
                  style={{ width: 80 }}
                />
                <span style={{ color: '#888', fontSize: 11 }}>{Math.round((settings[key] ?? 0.95) * 100)}%</span>
              </Space>
            ))}
          </div>
        </Card>
      </Space>

      <Modal
        title={
          <Space>
            <SwapOutlined />
            {t('settings.pluginMigration')}
            {migrationReport?.summary.dry_run ? <Tag color="blue">{t('settings.pluginMigrationPreview')}</Tag> : <Tag color="green">{t('settings.pluginMigrationApplied')}</Tag>}
          </Space>
        }
        open={migrationModalOpen}
        onCancel={() => setMigrationModalOpen(false)}
        width={680}
        footer={
          migrationReport?.summary.dry_run ? (
            <Space>
              <Button onClick={() => setMigrationModalOpen(false)}>{t('common.cancel') || '취소'}</Button>
              <Button type="primary" loading={migrationLoading} onClick={handleMigrationApply}>
                {t('settings.pluginMigrationApply')}
              </Button>
            </Space>
          ) : (
            <Button onClick={() => setMigrationModalOpen(false)}>{t('common.close') || '닫기'}</Button>
          )
        }
      >
        {migrationReport && (
          <Space direction="vertical" style={{ width: '100%' }} size="small">
            {migrationReport.summary.total_changes === 0 ? (
              <Alert type="success" showIcon message={t('settings.pluginMigrationNoChange')} />
            ) : (
              <Alert
                type={migrationReport.summary.dry_run ? 'info' : 'success'}
                showIcon
                message={`Files: ${migrationReport.summary.files_changed} · Changes: ${migrationReport.summary.total_changes}`}
              />
            )}

            {Object.keys(migrationReport.summary.module_renames || {}).length > 0 && (
              <div>
                <Text strong style={{ fontSize: 12 }}>Module renames</Text>
                <div style={{ marginTop: 4 }}>
                  {Object.entries(migrationReport.summary.module_renames).map(([from, to]) => (
                    <Tag key={from}>{from} → {to}</Tag>
                  ))}
                </div>
              </div>
            )}

            <div style={{ fontSize: 12 }}>
              <div>auxiliary_devices.json: <b>{migrationReport.auxiliary_devices.changed}</b> change(s) {!migrationReport.summary.dry_run && migrationReport.auxiliary_devices.changed > 0 && <Tag color="green">saved</Tag>}</div>
              <div>scan_settings.json: <b>{migrationReport.scan_settings.changed}</b> change(s) {!migrationReport.summary.dry_run && migrationReport.scan_settings.changed > 0 && <Tag color="green">saved</Tag>}</div>
              <div>device_catalog.json: <b>{migrationReport.device_catalog.changed}</b> change(s) {!migrationReport.summary.dry_run && migrationReport.device_catalog.changed > 0 && <Tag color="green">saved</Tag>}</div>
            </div>

            {migrationReport.scenarios.filter(s => s.changed > 0 || s.unmapped_functions.length > 0).length > 0 && (
              <div>
                <Text strong style={{ fontSize: 12 }}>Scenarios</Text>
                <div style={{ maxHeight: 220, overflowY: 'auto', marginTop: 4, padding: '4px 8px', background: 'rgba(0,0,0,0.03)', borderRadius: 4, fontSize: 12 }}>
                  {migrationReport.scenarios
                    .filter(s => s.changed > 0 || s.unmapped_functions.length > 0)
                    .map(s => (
                      <div key={s.file} style={{ marginBottom: 6 }}>
                        <div>
                          <Tag>{s.file}</Tag>
                          <b>{s.changed}</b> change(s)
                          {!migrationReport.summary.dry_run && s.saved && <Tag color="green" style={{ marginLeft: 4 }}>saved</Tag>}
                        </div>
                        {s.unmapped_functions.length > 0 && (
                          <div style={{ marginLeft: 16, marginTop: 2, color: '#d4380d' }}>
                            <Text type="warning" style={{ fontSize: 11 }}>⚠ {t('settings.pluginMigrationUnmapped')}:</Text>
                            <div style={{ marginLeft: 8 }}>
                              {s.unmapped_functions.map((u, i) => (
                                <div key={i} style={{ fontSize: 11 }}>
                                  step #{u.step_id} — {u.module}.{u.function}()
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    ))}
                </div>
              </div>
            )}
          </Space>
        )}
      </Modal>
    </div>
  );
}
