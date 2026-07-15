# Roadmap

## Google Cloud Storage

Google Cloud Storage (GCS) bucket integration is deferred. A future design may add typed bucket listing, resumable upload/download, checkpoint synchronization, IAM guidance, and explicit billing/project selection. It must not reuse or expose Colab OAuth material without a reviewed authentication and permission model.

The current persistent-storage workflow uses only the protected Google Drive folder `MyDrive/codex-colab`.
