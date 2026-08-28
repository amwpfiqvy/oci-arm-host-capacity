<?php

require_once 'vendor/autoload.php';

use App\OciConfig;
use App\OciApi;

// Cargar variables de entorno manualmente (más confiable que Dotenv en CI)
function loadEnv($file) {
    if (!file_exists($file)) {
        throw new Exception("Archivo .env no encontrado: $file");
    }
    
    $lines = file($file, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
    foreach ($lines as $line) {
        $line = trim($line);
        // Ignorar comentarios y líneas vacías
        if (empty($line) || strpos($line, '#') === 0) {
            continue;
        }
        
        // Parsear KEY=VALUE
        if (strpos($line, '=') !== false) {
            list($key, $value) = explode('=', $line, 2);
            $key = trim($key);
            $value = trim($value);
            
            // Remover comillas si existen
            if ((strpos($value, '"') === 0 && strrpos($value, '"') === strlen($value) - 1) ||
                (strpos($value, "'") === 0 && strrpos($value, "'") === strlen($value) - 1)) {
                $value = substr($value, 1, -1);
            }
            
            putenv("$key=$value");
            $_ENV[$key] = $value;
        }
    }
}

// Cargar .env
try {
    loadEnv(__DIR__ . '/.env');
} catch (Exception $e) {
    echo "Error cargando .env: " . $e->getMessage() . "\n";
    exit(1);
}

// Verificar variables requeridas
$required = [
    'OCI_REGION', 'OCI_USER_ID', 'OCI_TENANCY_ID', 'OCI_KEY_FINGERPRINT',
    'OCI_PRIVATE_KEY_FILENAME', 'OCI_SUBNET_ID', 'OCI_IMAGE_ID', 'OCI_SSH_PUBLIC_KEY'
];

$missing = [];
foreach ($required as $var) {
    if (empty(getenv($var))) {
        $missing[] = $var;
    }
}

if (!empty($missing)) {
    echo "Error: Faltan variables de entorno: " . implode(', ', $missing) . "\n";
    exit(1);
}

// Configuración de instancia (leída de .env / variables de entorno)
$ocpus = (int) (getenv('OCI_OCPUS') ?: '1');
$memoryInGbs = (int) (getenv('OCI_MEMORY_IN_GBS') ?: '6');
$maxInstances = (int) (getenv('OCI_MAX_INSTANCES') ?: '1');
$bootVolumeSize = getenv('OCI_BOOT_VOLUME_SIZE_IN_GBS') ?: '50';

echo "OCI ARM Host Capacity Checker\n";
echo "Region: " . getenv('OCI_REGION') . "\n";
echo "Shape: VM.Standard.A1.Flex\n";
echo "OCPUs: {$ocpus}, Memory: {$memoryInGbs}GB\n";
echo "Boot Volume Size: {$bootVolumeSize}GB\n";
echo "Max instances: {$maxInstances}\n";
echo "---\n";

$config = new OciConfig(
    getenv('OCI_REGION'),
    getenv('OCI_USER_ID'),
    getenv('OCI_TENANCY_ID'),
    getenv('OCI_KEY_FINGERPRINT'),
    getenv('OCI_PRIVATE_KEY_FILENAME'),
    getenv('OCI_AVAILABILITY_DOMAIN') ?: null,
    getenv('OCI_SUBNET_ID'),
    getenv('OCI_IMAGE_ID'),
    $ocpus,
    $memoryInGbs,
    $bootVolumeSize
);

$shape = 'VM.Standard.A1.Flex';
$sshKey = getenv('OCI_SSH_PUBLIC_KEY');

$api = new OciApi($config);

// Verificar instancias existentes
$instances = $api->getInstances($config);
$existingMsg = $api->checkExistingInstances($config, $instances, $shape, $maxInstances);

if ($existingMsg) {
    echo "$existingMsg\n";
    exit(0);
}

// Assign the first available stable hostname: apq1, then apq2.
$runningCount = 0;
$activeCount = 0;
$usedNames = [];
foreach ($instances as $instance) {
    if (($instance['shape'] ?? null) !== $shape) {
        continue;
    }

    $lifecycleState = $instance['lifecycleState'] ?? null;
    if ($lifecycleState === 'RUNNING') {
        $runningCount++;
    }
    if ($lifecycleState !== 'TERMINATED') {
        $activeCount++;
        $recognizedName = false;
        $name = $instance['displayName'] ?? '';
        if (in_array($name, ['apq1', 'apq2'], true)) {
            $usedNames[$name] = true;
            $recognizedName = true;
        }
        $hostnameLabel = $instance['createVnicDetails']['hostnameLabel'] ?? '';
        if (in_array($hostnameLabel, ['apq1', 'apq2'], true)) {
            $usedNames[$hostnameLabel] = true;
            $recognizedName = true;
        }
        // Older instances used a date-based name; reserve their ordinal slot.
        if (!$recognizedName && $activeCount <= 2) {
            $usedNames['apq' . $activeCount] = true;
        }
    }
}
$instanceName = null;
foreach (['apq1', 'apq2'] as $candidate) {
    if (!isset($usedNames[$candidate])) {
        $instanceName = $candidate;
        break;
    }
}
if ($instanceName === null) {
    echo "No available instance name (apq1/apq2) for a new A1 instance.\n";
    exit(1);
}
echo "Next instance name: {$instanceName}\n";

// Obtener dominios de disponibilidad
$availabilityDomains = $config->availabilityDomains;
if (empty($availabilityDomains)) {
    echo "Fetching availability domains...\n";
    $availabilityDomains = $api->getAvailabilityDomains($config);
}

if (empty($availabilityDomains)) {
    echo "No availability domains found!\n";
    exit(1);
}

echo "Checking availability domains: " . implode(', ', (array)$availabilityDomains) . "\n";

// Intentar crear en cada dominio
foreach ((array)$availabilityDomains as $ad) {
    echo "\nTrying availability domain: $ad\n";
    
    try {
        $instance = $api->createInstance($config, $shape, $sshKey, $ad, $instanceName);
        echo "\n✅ SUCCESS! Instance created with {$bootVolumeSize}GB disk:\n";
        echo json_encode($instance, JSON_PRETTY_PRINT) . "\n";
        exit(0);
    } catch (\Exception $e) {
        $msg = $e->getMessage();
        echo "❌ Failed: $msg\n";
        
        // Si es error de capacidad, continuar con siguiente AD
        if (strpos($msg, 'Out of host capacity') !== false) {
            echo "(Out of capacity, trying next domain...)\n";
            sleep(2);
            continue;
        }
        
        // Otro error, detener
        exit(1);
    }
}

echo "\n⚠️  Out of capacity in all availability domains. Will retry later.\n";
exit(0);
