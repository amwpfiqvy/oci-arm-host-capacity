# OCI ARM Host Capacity Checker

Script automatizado para crear instancias ARM en Oracle Cloud Infrastructure (OCI) cuando haya capacidad disponible.

## ⚠️ IMPORTANTE - LEER ANTES

Este repositorio fue creado porque el original de [hitrov/oci-arm-host-capacity](https://github.com/hitrov/oci-arm-host-capacity) fue archivado.

**NO dejes corriendo este workflow indefinidamente** - Una vez que se cree la instancia, debes desactivar el workflow o eliminar el archivo `.github/workflows/oci-arm-capacity.yml` para cumplir con los términos de GitHub Actions.

---

## 📋 PASO A PASO COMPLETO (Para principiantes)

### Paso 1: Hacer Fork de este repositorio

1. **Ve a la página principal de este repositorio** (donde estás leyendo esto ahora)
2. **Haz clic en el botón "Fork"** (está arriba a la derecha, al lado de la estrella ⭐)
   ![Fork button location](https://docs.github.com/assets/cb-34352/images/help/repository/fork_button.png)
3. **Selecciona tu cuenta personal** cuando pregunte "Where should we fork this repository?"
4. **Espera unos segundos** - GitHub creará una copia en tu cuenta

✅ **Resultado:** Ahora tienes tu propia copia del repositorio en `https://github.com/TU_USUARIO/oci-arm-host-capacity`

---

### Paso 2: Configurar los Secrets (Variables secretas)

Los "Secrets" son variables que GitHub guarda de forma segura para que nadie pueda verlas.

#### 2.1 Ir a la configuración de Secrets

1. En TU fork (tu copia del repositorio), haz clic en la pestaña **"Settings"** (arriba)
2. En el menú de la izquierda, despliega **"Secrets and variables"** → haz clic en **"Actions"**
3. Haz clic en el botón verde **"New repository secret"**

#### 2.2 Crear cada Secret (repite 6 veces)

Debes crear estos 6 secrets uno por uno:

| Nombre del Secret | Valor a pegar | Descripción |
|-------------------|---------------|-------------|
| `OCI_PRIVATE_KEY` | El contenido completo de tu clave privada PEM | La que empieza con `-----BEGIN PRIVATE KEY-----` |
| `OCI_REGION` | `mx-monterrey-1` | Tu región de OCI |
| `OCI_USER_ID` | `ocid1.user.oc1..aaaaaaaa2z6gilahwf4ugypiwyhjyvv5vmmqpcpo64gylfg64t5gsmtz5qyq` | Tu ID de usuario |
| `OCI_TENANCY_ID` | `ocid1.tenancy.oc1..aaaaaaaawvdaxbojftjmwwcrwph2at3dk2tfnjimug6mbcwnhalqvf5pbnfq` | Tu ID de tenancy |
| `OCI_KEY_FINGERPRINT` | `d7:86:a8:f5:fd:f8:b9:5f:fd:49:2d:69:8c:22:1c:76` | Fingerprint de tu API key |
| `OCI_SUBNET_ID` | `ocid1.subnet.oc1.mx-monterrey-1.aaaaaaaasjth3zsi4wxrd5tpov35ehm5lakxro6vbekdrp5iaqbdtwms62ta` | ID de tu subnet |
| `OCI_IMAGE_ID` | `ocid1.image.oc1.mx-monterrey-1.aaaaaaaarnhypfk2m7lpt5d6jyclob7aiosiyi3ftkejw4hqijo4oyi5zufq` | ID de la imagen |
| `OCI_SSH_PUBLIC_KEY` | `ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCGn+6vQqQV3QNb6Yyk4eN0yfisKB+YKR7OXiZ5jOcQ+z0eN2yqGy0ZIzO0CVonoJPhCsH+Ia6SGs/4bvW13muFKxin+zi89dW5lylY1Bs+RbrzIHhNHn3cBCdR4KozC9qkudc9KTl1lJPSQ1qCuc9wdKCqgiPMAuCx0TbJFZVXMoLeeu9b8y9ApyEyzf2V9su6nfx8upV+vG+MDejd+XXh/ptLpaYDpZaBIZAimfgCUHK33/zZ08VTHAoin3mm0mYFWiYSLUcCj72ogz0PadkGb/wH5+STV8Hh8Mt0Y8KyZLlQmYBS44AiM74SNOrtzGFbVxQlN/LsD4PTe3un+Cal ssh-key-2026-02-25` | Tu clave SSH pública |

**Para crear cada uno:**
1. En "Name" escribe exactamente el nombre de la columna (ej: `OCI_PRIVATE_KEY`)
2. En "Secret" pega el valor correspondiente
3. Haz clic en **"Add secret"**
4. Repite para los 6 secrets

✅ **Resultado:** Tienes 8 secrets configurados (los 6 de arriba + OCI_IMAGE_ID + OCI_SSH_PUBLIC_KEY que ya están preconfigurados en el workflow)

#### 2.3 Configurar notificaciones por correo (opcional)

Después de crear una instancia, el workflow puede enviar un correo mediante SMTP. Añade estos secrets en **Settings** → **Secrets and variables** → **Actions**:

| Nombre del Secret | Ejemplo | Descripción |
|-------------------|---------|-------------|
| `SMTP_URL` | `smtps://smtp.gmail.com:465` | URL SMTP. También puedes usar `smtp://smtp.gmail.com:587` para STARTTLS |
| `SMTP_USERNAME` | `tu-correo@gmail.com` | Usuario SMTP |
| `SMTP_PASSWORD` | `contraseña-de-aplicación` | Contraseña SMTP o contraseña de aplicación |
| `SMTP_FROM` | `tu-correo@gmail.com` | Dirección del remitente |
| `SMTP_TO` | `tu-correo-personal@ejemplo.com` | Dirección que recibirá la notificación |

Con Gmail debes usar una **contraseña de aplicación**, no la contraseña normal de la cuenta. Si estos secrets no están configurados, la notificación se omite y el workflow continúa funcionando.

#### 2.4 Configurar notificaciones de ServerChan (opcional)

Inicia sesión en [ServerChan](https://sct.ftqq.com/sendkey), genera un SendKey y añade este secret en **Settings** → **Secrets and variables** → **Actions**:

| Nombre del Secret | Descripción |
|-------------------|-------------|
| `SERVERCHAN_SENDKEY` | SendKey de ServerChan, normalmente comienza con `SCT` |

Cuando se cree una instancia nueva, el workflow enviará una notificación mediante ServerChan. Si el secret no está configurado, la notificación se omite sin afectar al workflow.

---

### Paso 3: Activar el Workflow

1. En tu repositorio, haz clic en la pestaña **"Actions"** (arriba)
2. Verás un mensaje amarillo diciendo que los workflows están deshabilitados
3. Haz clic en **"I understand my workflows, go ahead and enable them"**
4. Verás el workflow "OCI ARM Host Capacity Checker" en la lista

✅ **Resultado:** El workflow está activo y se ejecutará automáticamente

---

### Paso 4: Verificar que funciona

El workflow se ejecuta automáticamente cada 5 minutos, pero puedes probarlo manualmente:

1. Ve a la pestaña **"Actions"**
2. Haz clic en **"OCI ARM Host Capacity Checker"** en el menú izquierdo
3. Haz clic en el botón **"Run workflow"** → **"Run workflow"** (a la derecha)
4. Espera 30-60 segundos y haz clic en la ejecución que aparece

**Resultados esperados:**
- 🔴 **Error "Out of host capacity"** = ¡Funciona correctamente! Solo falta que haya capacidad disponible
- 🟢 **Success** = ¡Instancia creada! Revisa tu consola de OCI

---

### Paso 5: Cuando se cree la instancia (IMPORTANTE)

**⚠️ DEBES detener el workflow después de crear la instancia:**

1. Ve a la pestaña **"Actions"**
2. Haz clic en **"OCI ARM Host Capacity Checker"**
3. Haz clic en los **tres puntos ...** a la derecha → **"Disable workflow"**

O elimina el archivo `.github/workflows/oci-arm-capacity.yml` y haz commit.

**¿Por qué?** Dejarlo corriendo indefinidamente viola los términos de GitHub Actions.

---

## 🔧 Configuración

Edita el archivo `.github/workflows/oci-arm-capacity.yml` si quieres cambiar:

- **Frecuencia:** Línea `cron: '*/5 * * * *'` (cada 5 minutos)
- **Recursos:** Variables `OCI_OCPUS` y `OCI_MEMORY_IN_GBS` (actualmente 4 OCPUs / 24 GB)
- **Nombres:** las nuevas instancias usan `apq1` y `apq2` como nombre de instancia y etiqueta de hostname, generando `apq1.apqvcn.oraclevcn.com` y `apq2.apqvcn.oraclevcn.com` en la subred configurada.

---

## 📁 Estructura del repositorio

```
.
├── .github/
│   └── workflows/
│       └── oci-arm-capacity.yml    # Workflow de GitHub Actions
├── src/
│   ├── OciApi.php                  # Clase principal para API de OCI
│   ├── OciConfig.php               # Configuración
│   ├── HttpClient.php              # Cliente HTTP
│   └── ...                         # Otras clases necesarias
├── index.php                       # Script principal
├── composer.json                   # Dependencias PHP
└── README.md                       # Este archivo
```

---

## 🆘 Solución de problemas

### Error "Failed to verify the HTTP(S) Signature"
- Verifica que el `OCI_PRIVATE_KEY` esté completo (incluyendo las líneas `-----BEGIN PRIVATE KEY-----` y `-----END PRIVATE KEY-----`)
- Verifica que el fingerprint coincida exactamente

### Error "NotAuthorizedOrNotFound"
- Verifica que los OCIDs (user, tenancy, subnet, image) sean correctos
- Asegúrate de que la API key tenga los permisos necesarios en OCI

### El workflow no se ejecuta
- Ve a Settings → Actions → General → "Workflow permissions" → Selecciona "Read and write permissions"

---

## 📄 Licencia

MIT License - Basado en el trabajo original de Alexander Hitrov
