/** Tipos compartilhados pelo processo principal e pelo preload do Electron. */
export interface UpdateBackupFile {
  path: string;
  bytes: number;
  sha256: string;
}

export interface UpdateBackupEntry {
  id: string;
  createdAt: string;
  appVersion: string;
  path: string;
  bytes: number;
  sha256: string;
  files: readonly UpdateBackupFile[];
}
