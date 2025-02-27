#!/usr/bin/env python

from geometry_msgs.msg import WrenchStamped
import math
import rospy
from pr2_fingertip_sensors.msg import PR2FingertipSensor
from sensor_msgs.msg import Imu, PointCloud2, PointField
import sensor_msgs.point_cloud2 as pc2
from std_msgs.msg import Float32, Header


class ConvertPFS(object):
    """
    Convert PR2FingertipSensor message into the following messages
    - proximity_cloud(sensor_msgs/PointCloud2), pointcloud calculated from each proximity sensor. One topic per proximity sensor.
    - proximity_distance(std_msgs/Float32), distance calculated from each proximity sensor. One topic per proximity sensor.
    - wrench(geometry_msgs/WrenchStamped), wrench value. One topic per PFS board.
    - force(geometry_msgs/WrenchStamped), calibrated force sensor value. One topic per force sensor.
    - imu(sensor_msgs/Imu), IMU value. One topic per PFS A board.
    """
    def __init__(self):
        self.grippers = ['l_gripper', 'r_gripper']
        self.fingertips = ['l_fingertip', 'r_fingertip']
        self.parts = ['pfs_a_front', 'pfs_b_top', 'pfs_b_back', 'pfs_b_left', 'pfs_b_right']
        self.fields = [PointField(name='x', offset=0, datatype=PointField.FLOAT32, count=1),
                       PointField(name='y', offset=4, datatype=PointField.FLOAT32, count=1),
                       PointField(name='z', offset=8, datatype=PointField.FLOAT32, count=1)]
        self.pfs_params = rospy.get_param('/pfs')
        rospy.loginfo('Load pfs calibration params')
        rospy.loginfo(self.pfs_params)
        self.pub = {}
        for gripper in self.grippers:
            self.pub[gripper] = {}
            for fingertip in self.fingertips:
                # Publisher for proximity, force and imu
                self.pub[gripper][fingertip] = {}
                for part in self.parts:
                    self.pub[gripper][fingertip][part] = {}
                    sensors = ['proximity_distance', 'proximity_cloud', 'wrench', 'force', 'imu']
                    msg_types = [Float32, PointCloud2, WrenchStamped, WrenchStamped, Imu]
                    for sensor, msg_type in zip(sensors, msg_types):
                        if sensor == 'imu' and part != 'pfs_a_front':
                            # PFS B does not have IMU
                            continue
                        # For imu and wrench, publish value for each board
                        if sensor in ['imu', 'wrench']:
                            self.pub[gripper][fingertip][part][sensor] = rospy.Publisher(
                                '/pfs/{}/{}/{}/{}'.format(
                                    gripper, fingertip, part, sensor),
                                msg_type, queue_size=1)
                        # For proximity and force sensors, publish value for each sensor
                        if sensor in ['proximity_distance', 'proximity_cloud', 'force']:
                            self.pub[gripper][fingertip][part][sensor] = {}
                            for i in range(self.sensor_num(part)):
                                self.pub[gripper][fingertip][part][sensor][i] = rospy.Publisher(
                                    '/pfs/{}/{}/{}/{}/{}'.format(
                                        gripper, fingertip, part, sensor, i),
                                    msg_type, queue_size=1)
                # Subscriber
                # Create subscribers at the last of __init__ to avoid
                # 'object has no attribute ...' error
                rospy.Subscriber(
                    '/pfs/{}/{}'.format(gripper, fingertip),
                    PR2FingertipSensor, self.cb, (gripper, fingertip),
                    queue_size=1)

    def cb(self, msg, args):
        """
        publish each sensor data
        args must be tuple of (gripper, fingertip)
        """
        gripper = args[0]
        fingertip = args[1]
        # Publish proximity distance and proximity cloud
        self.publish_proximity(msg, gripper, fingertip)
        # Publish each force sensor value and publish wrench value for each board
        self.publish_force(msg, gripper, fingertip)
        # Publish imu value for PFS A board
        self.publish_imu(msg, gripper, fingertip)

    def sensor_num(self, part):
        """
        Args
        part: 'pfs_a_front', 'pfs_b_top', 'pfs_b_back', 'pfs_b_left' or 'pfs_b_right'

        Return
        sensor_num: The number of sensors in the specified PCB.
        """
        if part == 'pfs_a_front':
            sensor_num = 8
        else:
            sensor_num = 4
        return sensor_num

    def sensor_index(self, part, i):
        """
        Args
        part: 'pfs_a_front', 'pfs_b_top', 'pfs_b_back', 'pfs_b_left' or 'pfs_b_right'
        i: The index of sensor in each PCB. 0~7 for pfs_a_front. 0~3 for pfs_b.

        Return
        index: The index of sensor in all PCBs. This value is between 0~23.
        """
        if part == 'pfs_a_front':
            index = i
        elif part == 'pfs_b_top':
            index = 8 + i
        elif part == 'pfs_b_back':
            index = 12 + i
        elif part == 'pfs_b_left':
            index = 16 + i
        elif part == 'pfs_b_right':
            index = 20 + i
        return index

    def proximity_to_distance(self, proximity, gripper, fingertip, part, i):
        """
        Args
        proximity: The raw proximity value
        gripper: 'l_gripper' or 'r_gripper'
        fingertips: 'l_fingertip' or 'r_fingertip'
        part: 'pfs_a_front', 'pfs_b_top', 'pfs_b_back', 'pfs_b_left' or 'pfs_b_right'
        i: The index of sensor in each PCB. 0~7 for pfs_a_front. 0~3 for pfs_b.

        Return
        distance: Distance calculated from proximity value [m]
        """
        index = self.sensor_index(part, i)
        # Create method for conversion
        # I = (a / d^2) + b
        a = self.pfs_params[gripper][fingertip]['proximity_a'][index]
        b = self.pfs_params[gripper][fingertip]['proximity_b'][index]
        if a == 0:
            # This means the calibration process was not done or failed.
            distance = float('inf')
        else:
            distance = math.sqrt(
                a / max(0.1, proximity - b))  # unit: [m]
        return distance

    def publish_proximity(self, msg, gripper, fingertip):
        """
        Publish the following topics
        - proximity_cloud(sensor_msgs/PointCloud2), pointcloud calculated from each proximity sensor. One topic per proximity sensor.
        - proximity_distance(std_msgs/Float32), distance calculated from each proximity sensor. One topic per proximity sensor.
        """
        header = Header()
        header.stamp = msg.header.stamp
        for part in self.parts:
            frame_id_base = '/' + gripper + '_' + fingertip + '_' + part
            sensor_num = self.sensor_num(part)
            for i in range(sensor_num):
                header.frame_id = frame_id_base + '_' + str(i)
                index = self.sensor_index(part, i)  # index: 0~23
                # Convert proximity into PointCloud2
                distance = self.proximity_to_distance(
                    msg.proximity[index], gripper, fingertip, part, i)
                # Distance is under 0.1[m], it is regarded as reliable
                if distance < 0.1:
                    dist_msg = Float32(data=distance)
                    self.pub[gripper][fingertip][part]['proximity_distance'][i].publish(dist_msg)
                    point = [0, 0, distance]
                    prox_msg = pc2.create_cloud(header, self.fields, [point])
                    self.pub[gripper][fingertip][part]['proximity_cloud'][i].publish(prox_msg)

    def publish_force(self, msg, gripper, fingertip):
        """
        Publish the following topics
        - wrench(geometry_msgs/WrenchStamped), wrench value. One topic per PFS board.
        - force(std_msgs/Float32), force value. One topic per force sensor.

        Force Sensor (HSFPAR003A) documentation
        https://tech.alpsalpine.com/cms.media/product_spec_hsfpar003a_ja_4ea3fef0f5.pdf

        Equation for voltage [mV] is following
        - (force_sensor_value - preload) * adc_resolution = gain * force[N] * sensitivity
        - force[N] = 0.105 * (force_sensor_value - preload) / sensitivity

        where the values of the variables are as follows
        - sensitivity
          3.7[mV/N] (min 2.7, max 4.7)
          This is different among force sensors, so this is managed by rosparam
        - gain:
          1 + (100000[ohm] / 15000[ohm]) = 7.6666
        - adc_resolution [mV]:
          3.3[V] * 1000 / 4096 (12bit) = 0.806
        """
        header = Header()
        header.stamp = msg.header.stamp
        for part in self.parts:
            frame_id_base = '/' + gripper + '_' + fingertip + '_' + part
            sensor_num = self.sensor_num(part)
            average_force = 0.0
            for i in range(sensor_num):
                index = self.sensor_index(part, i)  # index: 0~23
                frame_id = frame_id_base + '_' + str(i)
                # Publish each force sensor value
                preload = self.pfs_params[gripper][fingertip]['preload'][index]
                sensitivity = self.pfs_params[gripper][fingertip]['sensitivity'][index]
                force = 0.105 * (msg.force[index] - preload) / sensitivity
                force_msg = WrenchStamped()
                header.frame_id = frame_id
                force_msg.header = header
                force_msg.wrench.force.z = force
                self.pub[gripper][fingertip][part]['force'][i].publish(force_msg)
                average_force += force / float(sensor_num)
            # Publish force on each PFS board
            board_force_msg = WrenchStamped()
            header.frame_id = frame_id_base
            board_force_msg.header = header
            board_force_msg.wrench.force.z = average_force
            self.pub[gripper][fingertip][part]['wrench'].publish(
                board_force_msg)

    def publish_imu(self, msg, gripper, fingertip):
        """
        Publish the following topic
        - imu(sensor_msgs/Imu), IMU value. One topic per PFS A board.
        """
        imu = msg.imu
        # Gyro
        gyro = imu.angular_velocity
        gyro_scale = 0.001  # TODO: Proper unit?
        msg.imu.angular_velocity.x = gyro.x * gyro_scale
        msg.imu.angular_velocity.y = gyro.y * gyro_scale
        msg.imu.angular_velocity.z = gyro.z * gyro_scale
        # Acceleration
        acc = imu.linear_acceleration
        acc_scale = 0.001  # TODO: Proper unit?
        msg.imu.linear_acceleration.x = acc.x * acc_scale
        msg.imu.linear_acceleration.y = acc.y * acc_scale
        msg.imu.linear_acceleration.z = acc.z * acc_scale
        self.pub[gripper][fingertip]['pfs_a_front']['imu'].publish(msg.imu)


if __name__ == '__main__':
    rospy.init_node('convert_pfs')
    pp = ConvertPFS()
    rospy.spin()
