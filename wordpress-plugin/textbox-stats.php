<?php
/**
 * Plugin Name: Textbox Stats
 * Description: Provides a small REST API for Textbox to read today's WordPress views without Jetpack.
 * Version: 1.0.0
 * Author: Textbox
 */

if (!defined('ABSPATH')) {
    exit;
}

const TEXTBOX_STATS_OPTION = 'textbox_stats_daily';
const TEXTBOX_STATS_VERSION = '1.0.0';

function textbox_stats_today_key(): string {
    return current_time('Y-m-d');
}

function textbox_stats_get_daily(): array {
    $daily = get_option(TEXTBOX_STATS_OPTION, []);
    return is_array($daily) ? $daily : [];
}

function textbox_stats_increment(): void {
    if (is_admin() || wp_doing_ajax() || wp_doing_cron()) {
        return;
    }
    if (!is_singular()) {
        return;
    }
    if (is_user_logged_in() && current_user_can('edit_posts')) {
        return;
    }

    $post_id = get_queried_object_id();
    if (!$post_id) {
        return;
    }

    $today = textbox_stats_today_key();
    $daily = textbox_stats_get_daily();
    $daily[$today] = isset($daily[$today]) ? (int) $daily[$today] + 1 : 1;

    // Keep the option small. Home only needs today, but retaining 45 days helps debugging.
    krsort($daily);
    $daily = array_slice($daily, 0, 45, true);
    update_option(TEXTBOX_STATS_OPTION, $daily, false);

    $post_key = 'textbox_stats_' . $today;
    $post_views = (int) get_post_meta($post_id, $post_key, true);
    update_post_meta($post_id, $post_key, $post_views + 1);
}
add_action('template_redirect', 'textbox_stats_increment', 20);

function textbox_stats_rest_permission(): bool {
    return current_user_can('edit_posts');
}

function textbox_stats_today_response(WP_REST_Request $request): WP_REST_Response {
    $today = textbox_stats_today_key();
    $daily = textbox_stats_get_daily();
    $views = isset($daily[$today]) ? (int) $daily[$today] : 0;

    return new WP_REST_Response([
        'ok' => true,
        'provider' => 'textbox-stats',
        'version' => TEXTBOX_STATS_VERSION,
        'date' => $today,
        'views' => $views,
    ], 200);
}

function textbox_stats_register_routes(): void {
    register_rest_route('textbox/v1', '/stats/today', [
        'methods' => 'GET',
        'callback' => 'textbox_stats_today_response',
        'permission_callback' => 'textbox_stats_rest_permission',
    ]);
}
add_action('rest_api_init', 'textbox_stats_register_routes');
