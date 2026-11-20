package org.beamcam

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.Network
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings as AndroidSettings
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.outlined.Videocam
import androidx.compose.material.icons.outlined.VideocamOff
import androidx.compose.material.icons.outlined.WifiOff
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalWindowInfo
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.launch
import java.net.Inet4Address
import java.net.NetworkInterface

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        setContent { BeamcamTheme { App() } }
    }

    override fun onStart() {
        super.onStart()
        WebcamState.appVisible = true
    }

    override fun onStop() {
        WebcamState.appVisible = false
        super.onStop()
    }
}

private val requiredPermissions = buildList {
    add(Manifest.permission.CAMERA)
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) add(Manifest.permission.POST_NOTIFICATIONS)
}.toTypedArray()

private fun Context.hasCamera() =
    ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun App() {
    val context = LocalContext.current
    val settings = remember { Settings(context) }
    val state by WebcamState.state.collectAsStateWithLifecycle()
    var cameraGranted by remember { mutableStateOf(context.hasCamera()) }
    var askedOnce by remember { mutableStateOf(false) }
    val snackbar = remember { SnackbarHostState() }
    val forgotMessage = stringResource(R.string.forgot_computers)
    val scope = rememberCoroutineScope()

    val permissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        askedOnce = true
        cameraGranted = context.hasCamera()
        if (cameraGranted) WebcamService.start(context)
    }

    // Returning from system settings may have changed the permission; the service is (re)started
    // whenever the app is opened so the phone is discoverable without extra taps.
    LifecycleEventEffect(Lifecycle.Event.ON_START) {
        cameraGranted = context.hasCamera()
        if (cameraGranted && WebcamState.state.value.status == WebcamStatus.Stopped) WebcamService.start(context)
    }

    LaunchedEffect(state.message) {
        state.message?.let {
            snackbar.showSnackbar(it)
            WebcamState.dismissMessage()
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.app_name)) },
                actions = {
                    if (cameraGranted) {
                        OverflowMenu(
                            onForget = {
                                settings.forgetComputers()
                                scope.launch { snackbar.showSnackbar(forgotMessage) }
                            },
                            onTurnOff = { WebcamService.stop(context) },
                            canTurnOff = state.status != WebcamStatus.Stopped,
                        )
                    }
                },
            )
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            val status = state.status
            when {
                !cameraGranted -> PermissionScreen(
                    showSettings = askedOnce,
                    onAllow = { permissionLauncher.launch(requiredPermissions) },
                    onOpenSettings = {
                        context.startActivity(
                            Intent(AndroidSettings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", context.packageName, null)),
                        )
                    },
                )
                status is WebcamStatus.Streaming -> StreamingScreen(
                    computerName = status.computerName,
                    facing = state.facing,
                    onFacing = { WebcamService.setFacing(context, it) },
                    onStop = { WebcamService.disconnect(context) },
                )
                status == WebcamStatus.Stopped -> OffScreen(onTurnOn = { WebcamService.start(context) })
                else -> ReadyScreen(deviceName = settings.deviceName, port = state.port)
            }
        }
    }

    val pending = state.status as? WebcamStatus.AwaitingApproval
    if (pending != null) {
        AlertDialog(
            onDismissRequest = {},
            icon = { Icon(Icons.Outlined.Videocam, contentDescription = null) },
            title = { Text(stringResource(R.string.approval_title, pending.computerName), textAlign = TextAlign.Center) },
            text = { Text(stringResource(R.string.approval_text, pending.code)) },
            confirmButton = { Button(onClick = { WebcamState.respondToApproval(true) }) { Text(stringResource(R.string.action_allow)) } },
            dismissButton = { TextButton(onClick = { WebcamState.respondToApproval(false) }) { Text(stringResource(R.string.action_deny)) } },
        )
    }
}

@Composable
private fun OverflowMenu(onForget: () -> Unit, onTurnOff: () -> Unit, canTurnOff: Boolean) {
    var open by remember { mutableStateOf(false) }
    IconButton(onClick = { open = true }) {
        Icon(Icons.Filled.MoreVert, contentDescription = stringResource(R.string.menu_more))
    }
    DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
        DropdownMenuItem(text = { Text(stringResource(R.string.menu_forget)) }, onClick = { open = false; onForget() })
        if (canTurnOff) {
            DropdownMenuItem(text = { Text(stringResource(R.string.action_turn_off)) }, onClick = { open = false; onTurnOff() })
        }
    }
}

@Composable
private fun Hero(icon: ImageVector, title: String, tint: Color = MaterialTheme.colorScheme.primary) {
    // A phone mounted sideways as a webcam has little height; keep the controls on screen.
    if (!isShortScreen()) {
        Spacer(Modifier.height(32.dp))
        Icon(icon, contentDescription = null, modifier = Modifier.size(96.dp), tint = tint)
        Spacer(Modifier.height(24.dp))
    }
    Text(title, style = MaterialTheme.typography.headlineMedium, textAlign = TextAlign.Center)
    Spacer(Modifier.height(12.dp))
}

@Composable
private fun isShortScreen(): Boolean {
    val height = LocalWindowInfo.current.containerSize.height
    return with(LocalDensity.current) { height.toDp() } < 480.dp
}

@Composable
private fun Body(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.bodyLarge,
        textAlign = TextAlign.Center,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.widthIn(max = 480.dp),
    )
}

@Composable
private fun PermissionScreen(showSettings: Boolean, onAllow: () -> Unit, onOpenSettings: () -> Unit) {
    Hero(Icons.Outlined.Videocam, stringResource(R.string.permission_title))
    Body(stringResource(R.string.permission_text))
    Spacer(Modifier.height(32.dp))
    Button(onClick = onAllow) { Text(stringResource(R.string.permission_button)) }
    if (showSettings) {
        Spacer(Modifier.height(16.dp))
        Body(stringResource(R.string.permission_denied))
        Spacer(Modifier.height(8.dp))
        OutlinedButton(onClick = onOpenSettings) { Text(stringResource(R.string.permission_settings_button)) }
    }
}

@Composable
private fun ReadyScreen(deviceName: String, port: Int) {
    val addresses = rememberLocalAddresses()
    if (addresses.isEmpty()) {
        Hero(Icons.Outlined.WifiOff, stringResource(R.string.ready_title), tint = MaterialTheme.colorScheme.error)
        Body(stringResource(R.string.no_network))
        return
    }
    Hero(Icons.Outlined.Videocam, stringResource(R.string.ready_title))
    Body(stringResource(R.string.ready_text, deviceName))
    Spacer(Modifier.height(8.dp))
    Body(stringResource(R.string.ready_same_wifi))
    Spacer(Modifier.height(40.dp))
    Card(modifier = Modifier.fillMaxWidth().widthIn(max = 480.dp)) {
        Column(Modifier.padding(20.dp).fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
            Text(stringResource(R.string.ready_manual), style = MaterialTheme.typography.bodyMedium, textAlign = TextAlign.Center)
            Spacer(Modifier.height(8.dp))
            SelectionContainer {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    addresses.forEach { address ->
                        Text(
                            if (port == Protocol.DEFAULT_PORT) address else "$address:$port",
                            style = MaterialTheme.typography.headlineSmall,
                            fontFamily = FontFamily.Monospace,
                            fontWeight = FontWeight.Medium,
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun StreamingScreen(computerName: String, facing: Facing, onFacing: (Facing) -> Unit, onStop: () -> Unit) {
    val context = LocalContext.current
    val options = remember {
        listOf(Facing.FRONT, Facing.BACK).filter { CameraStreamer.hasCamera(context, it) }
    }
    Hero(Icons.Outlined.Videocam, stringResource(R.string.streaming_title, computerName))
    Body(stringResource(R.string.streaming_text))
    val gap = if (isShortScreen()) 16.dp else 32.dp
    Spacer(Modifier.height(gap))
    if (options.size > 1) {
        SingleChoiceSegmentedButtonRow(Modifier.widthIn(max = 320.dp).fillMaxWidth()) {
            options.forEachIndexed { index, option ->
                SegmentedButton(
                    selected = facing == option,
                    onClick = { onFacing(option) },
                    shape = SegmentedButtonDefaults.itemShape(index, options.size),
                    modifier = Modifier.weight(1f),
                ) {
                    Text(
                        stringResource(if (option == Facing.FRONT) R.string.camera_front else R.string.camera_back),
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
        }
        Spacer(Modifier.height(gap / 2 + 8.dp))
    }
    OutlinedButton(onClick = onStop) { Text(stringResource(R.string.action_stop_streaming)) }
}

@Composable
private fun OffScreen(onTurnOn: () -> Unit) {
    Hero(Icons.Outlined.VideocamOff, stringResource(R.string.off_title), tint = MaterialTheme.colorScheme.onSurfaceVariant)
    Body(stringResource(R.string.off_text))
    Spacer(Modifier.height(32.dp))
    Row(horizontalArrangement = Arrangement.Center) {
        Button(onClick = onTurnOn) { Text(stringResource(R.string.action_turn_on)) }
    }
}

/** IPv4 addresses a computer on the same network (or on this phone's hotspot) can reach. */
@Composable
private fun rememberLocalAddresses(): List<String> {
    val context = LocalContext.current
    var addresses by remember { mutableStateOf(localAddresses()) }
    DisposableEffect(Unit) {
        val manager = context.getSystemService(ConnectivityManager::class.java)
        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) { addresses = localAddresses() }
            override fun onLost(network: Network) { addresses = localAddresses() }
            override fun onLinkPropertiesChanged(network: Network, lp: android.net.LinkProperties) { addresses = localAddresses() }
        }
        manager.registerDefaultNetworkCallback(callback)
        onDispose { manager.unregisterNetworkCallback(callback) }
    }
    return addresses
}

private val cellularInterface = Regex("^(rmnet|ccmni|pdp|v4-rmnet|dummy|tun|ppp).*")

private fun localAddresses(): List<String> = runCatching {
    NetworkInterface.getNetworkInterfaces().toList()
        .filter { it.isUp && !it.isLoopback && !cellularInterface.matches(it.name) }
        .flatMap { it.inetAddresses.toList() }
        .filterIsInstance<Inet4Address>()
        .filter { !it.isLinkLocalAddress }
        .map { it.hostAddress.orEmpty() }
        .filter { it.isNotEmpty() }
        .distinct()
}.getOrDefault(emptyList())

@Composable
private fun BeamcamTheme(content: @Composable () -> Unit) {
    val context = LocalContext.current
    val dark = isSystemInDarkTheme()
    val brand = Color(0xFF16A34A)
    val colors = when {
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> if (dark) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
        dark -> darkColorScheme(primary = Color(0xFF4ADE80))
        else -> lightColorScheme(primary = brand)
    }
    MaterialTheme(colorScheme = colors, content = content)
}
